"""Bounded, advisory hints for volumes used by this Core's temporary storage.

Hints do not authorize reading contents, cleaning directories or blocking work.
Registration failure never prevents a query. Only successfully registered roots
are covered; this is not discovery of every process/user's temporary locations.
"""
from collections import OrderedDict
import json
import os
from pathlib import Path
import stat
import threading
import time

MAX_ROOTS=32
MAX_BYTES=65536
SCHEMA='telyincore-temporary-volumes-v1'
FILE='temporary-roots.json'
_seen=OrderedDict()
_lock=threading.Lock()


def directory(path):
    path=Path(path).absolute()
    from .storage_watch import plain
    for part in reversed((path,*path.parents)):
        if not stat.S_ISDIR(plain(part).st_mode):raise ValueError('temporary root is not an ordinary directory')
    return path,path.stat()


def installation():
    # Embedders and isolated child-process tests may explicitly choose the
    # observer installation; normal runs derive it from the installed module.
    override=os.environ.get('TELYINCORE_STORAGE_OBSERVER_ROOT')
    return Path(override).absolute() if override else Path(__file__).resolve().parents[2]


def read(root):
    folder=Path(root)/'.storage-watch'
    if not os.path.lexists(folder):return {'schema':SCHEMA,'roots':[],'overflow':False}
    directory(folder)
    path=folder/FILE
    if not os.path.lexists(path):return {'schema':SCHEMA,'roots':[],'overflow':False}
    from .storage_watch import control,plain
    if plain(path).st_nlink!=1:raise ValueError('linked temporary hints')
    value=control(path)
    if value.get('schema')!=SCHEMA or type(value.get('overflow')) is not bool:raise ValueError('unknown temporary hints')
    rows=value.get('roots')
    if not isinstance(rows,list) or len(rows)>MAX_ROOTS:raise ValueError('invalid temporary hints')
    keys=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'path','device','inode'}:raise ValueError('invalid temporary hint')
        p=row['path']
        if not isinstance(p,str) or not p or len(p)>4096 or '\0' in p or not Path(p).is_absolute():raise ValueError('invalid temporary path')
        if any(type(row[k]) is not int or row[k]<0 for k in ('device','inode')):raise ValueError('invalid temporary identity')
        key=os.path.normcase(p)
        if key in keys:raise ValueError('duplicate temporary root')
        keys.add(key)
    return value


def observe(parent):
    """Best effort, nonblocking lock and bounded per-process retry throttling."""
    try:
        root=installation();parent,info=directory(parent)
        key=(str(root),os.path.normcase(str(parent)),info.st_dev,info.st_ino)
        now=time.monotonic()
        with _lock:
            if now<_seen.get(key,0):return True
        from .storage_watch import state_directory,exclusive,atomic
        directory(root)
        folder=state_directory(root)
        with exclusive(folder,name='temporary-roots.lock'):
            value=read(root);rows=value['roots'];identity={'path':str(parent),'device':info.st_dev,'inode':info.st_ino}
            found=next((i for i,row in enumerate(rows) if os.path.normcase(row['path'])==key[1]),None)
            accepted=True
            if found is not None:rows[found]=identity
            elif len(rows)<MAX_ROOTS:rows.append(identity)
            else:value['overflow']=True;accepted=False
            raw=json.dumps(value,ensure_ascii=True).encode()
            if len(raw)>MAX_BYTES:
                value=read(root);value['overflow']=True;raw=json.dumps(value,ensure_ascii=True).encode();accepted=False
            atomic(folder,FILE,raw)
        if accepted:
            with _lock:
                _seen[key]=now+60;_seen.move_to_end(key)
                while len(_seen)>64:_seen.popitem(last=False)
        return accepted
    except (OSError,ValueError,TypeError,RuntimeError):
        return False


def volumes(root, known, policy):
    """Observe registered directory identities and free space; never enumerate."""
    import shutil
    rows=[];alerts=[];errors=set()
    try:value=read(root)
    except (OSError,ValueError,TypeError):return [],[],{'temporary_registry_unavailable'}
    if value['overflow']:errors.add('temporary_registry_limit')
    for item in value['roots']:
        row={'path':item['path'],'free_bytes':None,'status':'unavailable'}
        try:
            path,info=directory(item['path'])
            if (info.st_dev,info.st_ino)!=(item['device'],item['inode']):raise ValueError('temporary directory changed')
            shared=info.st_dev in known
            free=known[info.st_dev] if shared else shutil.disk_usage(path).free
            if not shared:
                known[info.st_dev]=free
                if free is not None and free<policy.free_bytes:
                    alerts.append({'key':'registered_temporary_free:'+str(info.st_dev),'kind':'low_registered_temporary_space','bytes':free,'threshold':policy.free_bytes})
            row.update(free_bytes=free,status='observed' if free is not None else 'unavailable',same_observed_volume=shared)
            if free is None:errors.add('registered_temporary_volume_unavailable')
        except (OSError,ValueError):errors.add('registered_temporary_volume_unavailable')
        rows.append(row)
    return rows,alerts,errors
