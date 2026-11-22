"""Bounded first-cache maintenance using the retrieval model-content namespace.

Default is read-only. Existing incomplete active jars are never replaced; legacy
unbound jars are retained. Production appenders do not share a maintenance lock.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

from telyincore import embeddings


def _safe(root: Path, path: Path) -> Path:
    from runtime_identity import safe_path
    for part in (root,*root.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(),'st_file_attributes',0)&0x400):
            raise ValueError('Maintenance root must not be redirected')
    return safe_path(root,path)


def _snapshot(root: Path) -> list[tuple[str,str]]:
    index=_safe(root,root/'search/search.sqlite3')
    with closing(sqlite3.connect(index.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA temp_store=MEMORY')
        return [(str(key),str(body)) for key,body in connection.execute('SELECT key,body FROM the_lines ORDER BY key')]


def snapshot(root: Path) -> list[str]:
    return [body for _,body in _snapshot(Path(root).absolute())]


def _bytes(root: Path,path: Path):
    _safe(root,path)
    if not path.exists():return None
    with path.open('rb') as stream:
        info=os.fstat(stream.fileno());raw=stream.read()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise ValueError('Vector cache must be one regular file')
    after=path.stat()
    if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
        raise RuntimeError('Vector cache changed during read')
    return raw


def _valid(vector):
    if (not isinstance(vector,(list,tuple)) or len(vector)!=1024
            or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in vector)):
        raise ValueError('Invalid BGE-M3 vector dimension or value')
    if not .99<=math.fsum(float(x)**2 for x in vector)<=1.01:
        raise ValueError('Embedding must be normalized')


def _validate_existing(raw):
    if raw is None:return
    try:
        for line in raw.decode('utf-8').splitlines():
            if not line.strip():continue
            row=json.loads(line)
            if not isinstance(row,dict) or not isinstance(row.get('fingerprint'),str) or not row['fingerprint']:
                raise ValueError('Invalid existing vector cache')
            _valid(row.get('vector'))
    except (ValueError,TypeError,UnicodeError,OverflowError):
        raise ValueError('Invalid existing vector cache; preserve it for separate maintenance') from None


def bootstrap(root: Path, *, apply: bool=False,max_rows: int=64,
              max_characters: int=200_000,embedder=None,
              model_path: str=embeddings.DEFAULT_MODEL,batch_size: int=1,
              validate_before_commit=None,progress=None) -> dict:
    root=Path(root).absolute();_safe(root,root)
    if not root.is_dir():raise FileNotFoundError('Explicit runtime root is missing')
    if type(batch_size) is not int or not 1<=batch_size<=16:raise ValueError('Batch size must be between 1 and 16')
    if validate_before_commit is not None and not callable(validate_before_commit):raise TypeError('Verifier must be callable')
    content=embeddings._content_identity(model_path)
    base=_safe(root,root/'vectors')
    jar=_safe(root,embeddings._jar_path(base,model_path))
    pairs=_snapshot(root);rows=[body for _,body in pairs]
    before=_bytes(root,jar);_validate_existing(before)
    existing=embeddings.the_jar(base,model_path=model_path)
    if _bytes(root,jar)!=before:raise RuntimeError('Vector cache changed during reader snapshot')
    missing={embeddings.the_key(text):text for text in rows if embeddings.the_key(text) not in existing}
    report=dict(root=str(root),active_jar=str(jar),model_content_identity=content,indexed_rows=len(rows),
        missing_before=sum(embeddings.the_key(text) not in existing for text in rows),
        unique_missing=len(missing),written=0,applied=False,dimensions=1024)

    def checkpoint():
        embeddings._require_content(model_path,content)
        if embeddings._jar_path(base,model_path)!=jar:raise RuntimeError('Active vector path changed')
        if _snapshot(root)!=pairs:raise RuntimeError('Search snapshot changed; no cache published')
        current=_bytes(root,jar)
        if before is None and current is not None:raise FileExistsError('Concurrent active cache creator; do not overwrite')
        if current!=before:raise RuntimeError('Active cache changed; do not overwrite')
        if validate_before_commit is not None and validate_before_commit() is not True:
            raise RuntimeError('Maintenance identity/source snapshot changed')

    checkpoint()
    if not apply or not missing:
        report['missing_after']=report['missing_before']
        return report
    if before is not None:
        raise RuntimeError('Existing incomplete active jar: refusing to replace or race production appends')
    if not 0<len(missing)<=max_rows or sum(map(len,missing.values()))>max_characters:
        raise ValueError('Maintenance input exceeds explicit bounds')
    encoder=embedder if embedder is not None else lambda texts:embeddings.embed(texts,model_path=model_path)
    items=list(missing.items());made=[]
    for offset in range(0,len(items),batch_size):
        checkpoint();batch=items[offset:offset+batch_size]
        vectors=encoder([text for _,text in batch])
        if len(vectors)!=len(batch):raise ValueError('Embedding row count mismatch')
        for vector in vectors:_valid(vector)
        checkpoint();made.extend(zip((fp for fp,_ in batch),vectors))
        if progress is not None:progress(dict(completed=len(made),total=len(items)))
    checkpoint();_safe(root,jar.parent);jar.parent.mkdir(parents=True,exist_ok=True)
    fd,temp_name=tempfile.mkstemp(prefix='.bootstrap-',suffix='.tmp',dir=jar.parent)
    temporary=Path(temp_name)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:
            for fingerprint,vector in made:
                stream.write(json.dumps(dict(fingerprint=fingerprint,vector=vector),allow_nan=False)+'\n')
            stream.flush();os.fsync(stream.fileno())
        checkpoint()
        os.link(temporary,jar) # Atomic create-only: a competing target always wins.
    finally:
        temporary.unlink(missing_ok=True)
    current_pairs=_snapshot(root)
    embeddings._require_content(model_path,content)
    _validate_existing(_bytes(root,jar))
    current=embeddings.the_jar(base,model_path=model_path)
    remaining=sum(embeddings.the_key(body) not in current for _,body in current_pairs)
    report.update(applied=True,written=len(made),missing_after=remaining,
        index_changed_after_publish=current_pairs!=pairs,
        source_changed_after_publish=validate_before_commit is not None and validate_before_commit() is not True)
    # After a create-only commit, never remove a cache to fake a rollback.
    if report['index_changed_after_publish'] or report['source_changed_after_publish']:
        raise RuntimeError('Snapshot changed after publication; retain cache and recheck before further work')
    return report


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True,help='Explicit Bot runtime data root')
    parser.add_argument('--apply',action='store_true',help='Default is a read-only count')
    parser.add_argument('--max-rows',type=int,default=64)
    parser.add_argument('--batch-size',type=int,default=1)
    args=parser.parse_args()
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ['OMP_NUM_THREADS']='2';os.environ['MKL_NUM_THREADS']='2'
    if args.apply:
        import torch
        torch.set_num_threads(2)
    print(json.dumps(bootstrap(args.root,apply=args.apply,max_rows=args.max_rows,batch_size=args.batch_size),ensure_ascii=False))


if __name__=='__main__':main()
