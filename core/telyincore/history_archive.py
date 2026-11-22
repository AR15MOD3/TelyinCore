"""Opt-in, byte-exact archive candidates. No automatic production migration.

A reader is bound to a caller-pinned Generation and current grant, never to an
untrusted manifest's claimed authority. Original generations stay intact.
SQLite/gzip works on both supported runtimes without additional dependencies.
Seekable zstd is an optional comparison backend, not a default dependency.
"""
from contextlib import contextmanager, closing
from dataclasses import asdict
import bisect
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import zlib
from .source_bytes import (Generation, PlainReader, checked_open, safe_file,
                           require_grant, window, digest_file)
from .file_signature import signature

SCHEMA = 'telyincore-original-blocks-v1'
MAX_BLOCK = 1024**2
MAX_BLOCKS = 65536
MAX_MANIFEST = 16*1024**2


def _zstd():
    try:
        from compression import zstd
        return zstd, True
    except ImportError:
        try:
            import zstandard
            return zstandard, False
        except ImportError as error:
            raise ValueError('zstd codec unavailable; source preserved') from error


def encode(raw, codec):
    if codec == 'gzip':return gzip.compress(raw, compresslevel=6, mtime=0)
    if codec == 'zstd':
        z, standard = _zstd()
        return z.compress(raw, level=3) if standard else z.ZstdCompressor(level=3).compress(raw)
    raise ValueError('unsupported codec')


def decode(raw, codec, size):
    if type(size) is not int or not 0 < size <= MAX_BLOCK or len(raw) > MAX_BLOCK+65536:
        raise ValueError('invalid compressed block bounds')
    if codec == 'gzip':
        d=zlib.decompressobj(31);out=d.decompress(raw,size+1)
        if not d.eof or d.unused_data or d.unconsumed_tail:raise ValueError('invalid gzip block')
    elif codec == 'zstd':
        z,standard=_zstd()
        if standard:
            d=z.ZstdDecompressor(options={z.DecompressionParameter.window_log_max:23})
            out=d.decompress(raw,max_length=size+1)
            if not d.eof or d.unused_data:raise ValueError('invalid zstd block')
        else:
            params=z.get_frame_parameters(raw)
            if params.content_size!=size or params.window_size>8*1024**2:raise ValueError('invalid zstd frame bounds')
            out=z.ZstdDecompressor(max_window_size=8192).decompress(raw,max_output_size=size,allow_extra_data=False)
    else:raise ValueError('unsupported codec')
    if len(out)!=size:raise ValueError('decoded length mismatch')
    return out


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _directory(path):
    path=Path(path).absolute()
    for p in (path,*path.parents):
        s=p.lstat()
        if p.is_symlink() or getattr(s,'st_file_attributes',0)&0x400:raise ValueError('linked archive directory')
    if not path.is_dir():raise ValueError('archive parent missing')
    return path


def _sync_directory(path):
    if os.name!='nt':
        fd=os.open(path,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)


def _publish(pending, target):
    # No overwrite, including races; both files are on the same filesystem.
    os.link(pending,target)
    _sync_directory(target.parent)


def _connect(path):
    db=sqlite3.connect(Path(path).absolute().as_uri()+'?mode=ro',uri=True)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
        ticks=[0]
        def bounded_query():
            ticks[0]+=1
            return int(ticks[0]>10000)
        db.set_progress_handler(bounded_query,1000)
        schema=dict(db.execute("SELECT name,type FROM sqlite_master WHERE name IN ('metadata','blocks')"))
        if schema!={'metadata':'table','blocks':'table'}:raise ValueError('archive table schema')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MAX_BLOCK+65536)
        db.execute('PRAGMA cache_size=-1024')
        return db
    except BaseException:db.close();raise


class ArchiveReader:
    """Validate complete original once, then recheck source identity on every read.

    Construct a fresh reader after a source identity change; never keep using an
    old verified snapshot when a physical pack/manifest is replaced or edited.
    """
    def __init__(self, path, generation, grant, *, kind='sqlite'):
        require_grant(grant,generation)
        self.path=Path(path).absolute();self.generation=generation;self.grant=grant;self.kind=kind
        self.manifest_path=self.path.with_name(self.path.name+'.json') if kind=='seekable' else None
        safe_file(self.path);self.identity=signature(self.path)
        if kind=='sqlite':
            with checked_open(self.path,expected=self.identity), closing(_connect(self.path)) as db:
                row=db.execute('SELECT value FROM metadata WHERE key=?',('manifest',)).fetchone()
                if row is None or not isinstance(row[0],str) or len(row[0])>65536:raise ValueError('archive manifest missing')
                self.meta=json.loads(row[0])
                self.blocks=[];offset=0
                for i,(number,n,rawhash,packedhash,packedsize) in enumerate(db.execute('SELECT number,raw_size,raw_hash,packed_hash,length(body) FROM blocks ORDER BY number')):
                    if i>=MAX_BLOCKS or number!=i:raise ValueError('archive block sequence')
                    self.blocks.append([offset,n,packedsize,rawhash,packedhash]);offset+=n
                if db.execute('SELECT count(*) FROM metadata').fetchone()[0]!=1:raise ValueError('archive metadata schema')
        elif kind=='seekable':
            with checked_open(self.manifest_path) as stream:
                self.manifest_identity=signature(self.manifest_path)
                if self.manifest_identity[2]>MAX_MANIFEST:raise ValueError('archive manifest budget')
                raw=stream.read(self.manifest_identity[2]+1)
            if len(raw)>MAX_MANIFEST:raise ValueError('archive manifest budget')
            self.meta=json.loads(raw);self.blocks=self.meta.pop('blocks')
            if not isinstance(self.blocks,list) or len(self.blocks)>MAX_BLOCKS:raise ValueError('archive block budget')
        else:raise ValueError('unsupported archive kind')
        if not isinstance(self.meta,dict):raise ValueError('invalid archive manifest')
        Generation(**self.meta.get('generation',{}))
        if self.meta!={'schema':SCHEMA,'kind':kind,'codec':self.meta.get('codec'),'generation':asdict(generation)}:
            raise ValueError('archive logical identity mismatch')
        self.codec=self.meta['codec']
        if self.codec not in ('gzip','zstd') or (kind=='seekable' and self.codec!='zstd'):raise ValueError('archive codec mismatch')
        offset=0;packedoffset=0;self.starts=[];self.packed_starts=[]
        for row in self.blocks:
            if not isinstance(row,list) or len(row)!=5:raise ValueError('invalid block descriptor')
            start,n,packedsize,rh,ph=row
            if any(type(x) is not int for x in (start,n,packedsize)) or start!=offset or not 0<n<=MAX_BLOCK or not 0<packedsize<=MAX_BLOCK+65536:
                raise ValueError('invalid block extent')
            if any(not isinstance(h,str) or len(h)!=64 or any(c not in '0123456789abcdef' for c in h) for h in (rh,ph)):raise ValueError('invalid block digest')
            self.starts.append(offset);self.packed_starts.append(packedoffset);offset+=n;packedoffset+=packedsize
        if offset!=generation.size:raise ValueError('archive original length mismatch')
        if kind=='seekable':
            table=b''.join(struct.pack('<II',r[2],r[1]) for r in self.blocks)
            footer=struct.pack('<IBI',len(self.blocks),0,0x8F92EAB1)
            expected=struct.pack('<II',0x184D2A5E,len(table)+9)+table+footer
            if self.identity[2]!=packedoffset+len(expected):raise ValueError('seek table size mismatch')
            with self._access() as (stream,db):
                stream.seek(packedoffset)
                if stream.read(len(expected))!=expected:raise ValueError('seek table mismatch')
        digest=hashlib.sha256()
        with self._access() as handles:
            for i in range(len(self.blocks)):digest.update(self._block(i,*handles))
        if digest.hexdigest()!=generation.sha256:raise ValueError('archive original digest mismatch')

    @contextmanager
    def _access(self):
        require_grant(self.grant,self.generation)
        with checked_open(self.path,expected=self.identity) as stream:
            if self.kind=='sqlite':
                with closing(_connect(self.path)) as db:yield stream,db
            else:
                with checked_open(self.manifest_path,expected=self.manifest_identity):yield stream,None
        require_grant(self.grant,self.generation)

    def _block(self,index,stream,db):
        start,n,packedsize,rh,ph=self.blocks[index]
        if db is not None:
            row=db.execute('SELECT body FROM blocks WHERE number=?',(index,)).fetchone()
            if row is None or not isinstance(row[0],bytes):raise ValueError('archive block missing')
            packed=row[0]
        else:
            stream.seek(self.packed_starts[index]);packed=stream.read(packedsize)
        if len(packed)!=packedsize or _sha(packed)!=ph:raise ValueError('archive compressed digest mismatch')
        raw=decode(packed,self.codec,n)
        if _sha(raw)!=rh:raise ValueError('archive original block mismatch')
        return raw

    def read(self,offset,length):
        length=window(self.generation.size,offset,length)
        with self._access() as handles:
            if not length:return b''
            index=max(0,bisect.bisect_right(self.starts,offset)-1);pieces=[];end=offset+length
            while index<len(self.blocks) and self.starts[index]<end:
                raw=self._block(index,*handles);start=self.starts[index]
                pieces.append(raw[max(0,offset-start):end-start]);index+=1
            result=b''.join(pieces)
            if len(result)!=length:raise ValueError('archive read incomplete')
            return result

    def chunks(self):
        # Keep one connection while streaming; grant rechecked between blocks.
        with self._access() as handles:
            for i in range(len(self.blocks)):
                require_grant(self.grant,self.generation)
                yield self._block(i,*handles)
            require_grant(self.grant,self.generation)


def build(source, target, generation, grant, *, kind='sqlite', codec='gzip', block_size=262144, only_if_smaller=False):
    """Build one finite generation in a private pending file; never delete source.

    Target must not exist. No writer lease is stolen. The source identity and
    full original hash are checked before and after building and roundtrip.
    """
    if type(block_size) is not int or not 4096<=block_size<=MAX_BLOCK:raise ValueError('block size must be 4KiB..1MiB')
    if (generation.size+block_size-1)//block_size>MAX_BLOCKS:raise ValueError('archive block budget; leave source plain')
    if kind not in ('sqlite','seekable') or codec not in ('gzip','zstd') or (kind=='seekable' and codec!='zstd'):raise ValueError('invalid archive format')
    if codec=='zstd':_zstd()
    source=Path(source);target=Path(target).absolute();parent=_directory(target.parent)
    sidecar=target.with_name(target.name+'.json')
    if os.path.lexists(target) or (kind=='seekable' and os.path.lexists(sidecar)):raise FileExistsError(target)
    reader=PlainReader(source,generation,grant)
    fd,name=tempfile.mkstemp(prefix='.archive-pending-',dir=parent);os.close(fd);pending=Path(name)
    pending_manifest=pending.with_name(pending.name+'.json');db=None;out=None
    blocks=[];meta=dict(schema=SCHEMA,kind=kind,codec=codec,generation=asdict(generation))
    try:
        if kind=='sqlite':
            db=sqlite3.connect(pending);db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=FULL');db.execute('PRAGMA cache_size=-1024')
            db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('CREATE TABLE blocks(number INTEGER PRIMARY KEY,raw_size INTEGER NOT NULL,raw_hash TEXT NOT NULL,packed_hash TEXT NOT NULL,body BLOB NOT NULL)')
            db.execute('INSERT INTO metadata VALUES(?,?)',('manifest',json.dumps(meta,ensure_ascii=True)))
        else:out=pending.open('wb')
        for number,offset in enumerate(range(0,generation.size,block_size)):
            raw=reader.read(offset,block_size);packed=encode(raw,codec)
            if len(packed)>MAX_BLOCK+65536:raise ValueError('compressed block budget')
            rh,ph=_sha(raw),_sha(packed);blocks.append([offset,len(raw),len(packed),rh,ph])
            if db is not None:db.execute('INSERT INTO blocks VALUES(?,?,?,?,?)',(number,len(raw),rh,ph,packed))
            else:out.write(packed)
        if db is not None:db.commit();db.close();db=None
        else:
            table=b''.join(struct.pack('<II',r[2],r[1]) for r in blocks)
            out.write(struct.pack('<II',0x184D2A5E,len(table)+9)+table+struct.pack('<IBI',len(blocks),0,0x8F92EAB1))
            out.flush();os.fsync(out.fileno());out.close();out=None
            manifest=json.dumps(dict(meta,blocks=blocks),ensure_ascii=True,separators=(',',':')).encode()
            if len(manifest)>MAX_MANIFEST:raise ValueError('manifest budget')
            with pending_manifest.open('xb') as f:f.write(manifest);f.flush();os.fsync(f.fileno())
        with pending.open('r+b') as f:os.fsync(f.fileno())
        ArchiveReader(pending,generation,grant,kind=kind)  # complete roundtrip before publish
        reader.read(generation.size,0)  # source/permission unchanged, including empty generation
        packed_bytes = pending.stat().st_size
        if kind == 'seekable':
            packed_bytes += pending_manifest.stat().st_size
        if only_if_smaller and packed_bytes >= generation.size:
            return dict(kind=kind, codec=codec, generation=asdict(generation),
                        original_bytes=generation.size, archive_bytes=packed_bytes,
                        published=False, reason='no_size_saving', original_preserved=True)
        if kind=='seekable':
            # Commit manifest last. A crash can leave an unreferenced pack, but
            # readers reject it. Never auto-delete unfamiliar orphan files.
            _publish(pending,target)
            _publish(pending_manifest,sidecar)
        else:_publish(pending,target)
        return dict(kind=kind,codec=codec,generation=asdict(generation),path=str(target),
                    original_bytes=generation.size,archive_bytes=target.stat().st_size+(sidecar.stat().st_size if kind=='seekable' else 0),
                    blocks=len(blocks),original_preserved=True,published=True)
    except BaseException:
        # A published object may be visible to another reader. Leave it for
        # explicit recovery; do not unlink a target another process can replace.
        raise
    finally:
        if db is not None:db.close()
        if out is not None:out.close()
        pending.unlink(missing_ok=True);pending_manifest.unlink(missing_ok=True)
        # Only our private SQLite rollback journal, never any target's sidecars.
        pending.with_name(pending.name+'-journal').unlink(missing_ok=True)


def export(reader, target):
    """Write a verified, exclusive original copy. Caller chooses an offline path."""
    target=Path(target).absolute();parent=_directory(target.parent)
    if os.path.lexists(target):raise FileExistsError(target)
    fd,name=tempfile.mkstemp(prefix='.export-pending-',dir=parent);pending=Path(name)
    try:
        digest=hashlib.sha256();size=0
        with os.fdopen(fd,'wb') as out:
            for raw in reader.chunks():out.write(raw);digest.update(raw);size+=len(raw)
            out.flush();os.fsync(out.fileno())
        if (size,digest.hexdigest())!=(reader.generation.size,reader.generation.sha256):raise ValueError('export original mismatch')
        reader.read(reader.generation.size,0)
        _publish(pending,target)
    finally:pending.unlink(missing_ok=True)


def open_original(path, generation, grant, *, kind='plain'):
    """One byte-reader contract; logical identity and grants outlive placement."""
    return PlainReader(path,generation,grant) if kind=='plain' else ArchiveReader(path,generation,grant,kind=kind)
