"""Read-only v1 context-index relocation plan with complete explicit witnesses.

Never infers scope/branch from hashes, reads reasoning bodies, manufactures context
IDs or writes production data. The future coordinator must preserve session bytes,
RecordStore bytes, permissions and visibility, and journal every planned change.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from runtime_identity import safe_path


def _hash(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


def context_filename(session,scope,branch_context=None):
    key=[str(Path(session).resolve()),scope]
    if branch_context is not None:key.extend(['request-branch',branch_context])
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()+'.json'


@dataclass(frozen=True)
class ContextWitness:
    session_relative: str
    scope: str
    metadata_sha256: str
    branch_context: str | None = None


def plan_context_reindex(old_runtime,new_runtime,witnesses):
    old=Path(old_runtime).absolute();new=Path(new_runtime).absolute()
    safe_path(old.parent,old);safe_path(new.parent,new)
    if old.parent!=new.parent:raise ValueError('only same-parent runtime relocation is supported by this plan')
    if not old.is_dir():raise ValueError('source runtime missing')
    if new!=old and new.exists():raise ValueError('destination runtime already exists; never mix data')
    source=safe_path(old,old/'provider-state/contexts')
    target=safe_path(new,new/'provider-state/contexts')
    entries={}
    if source.exists():
        for path in source.iterdir():
            safe_path(old,path)
            if not path.is_file() or re.fullmatch(r'[0-9a-f]{64}\.json',path.name) is None:
                raise ValueError('unknown context index entry requires an adapter')
            entries[path.name]=path
    if not isinstance(witnesses,(list,tuple)) or len(witnesses)>1024:raise ValueError('invalid witness collection')
    covered=set();destinations=set();moves=[]
    for witness in witnesses:
        if (not isinstance(witness,ContextWitness) or not isinstance(witness.session_relative,str)
                or not isinstance(witness.scope,str) or len(witness.scope)>1024
                or not _hash(witness.metadata_sha256)
                or witness.branch_context is not None and not _hash(witness.branch_context)):
            raise ValueError('invalid context witness')
        relative=Path(witness.session_relative)
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('session witness escapes runtime')
        old_session=safe_path(old,old/relative);new_session=safe_path(new,new/relative)
        if not old_session.is_file():raise ValueError('witness session missing')
        filename=context_filename(old_session,witness.scope,witness.branch_context)
        if filename not in entries or filename in covered:raise ValueError('witness missing, duplicate or scope/branch incorrect')
        path=entries[filename]
        with path.open('rb') as stream:raw=stream.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:raise ValueError('context metadata size limit')
        if hashlib.sha256(raw).hexdigest()!=witness.metadata_sha256:raise ValueError('context metadata changed')
        def unique(pairs):
            data={}
            for key,value in pairs:
                if key in data:raise ValueError('duplicate context metadata field')
                data[key]=value
            return data
        meta=json.loads(raw,object_pairs_hook=unique)
        if (not isinstance(meta,dict) or set(meta)!={'version','context','records'}
                or type(meta['version']) is not int or meta['version']!=1 or not _hash(meta['context'])
                or not isinstance(meta['records'],list) or len(meta['records'])>65536
                or not all(_hash(value) for value in meta['records'])):
            raise ValueError('unsupported context metadata')
        destination=context_filename(new_session,witness.scope,witness.branch_context)
        if destination in destinations:raise ValueError('destination context collision')
        dest=safe_path(new,target/destination)
        if dest!=path and dest.exists():raise ValueError('destination index already exists; never overwrite')
        covered.add(filename);destinations.add(destination)
        if dest!=path:
            moves.append({'old_relative':'provider-state/contexts/'+filename,
                          'new_relative':'provider-state/contexts/'+destination,
                          'sha256':witness.metadata_sha256,'preserve_bytes':True})
    if covered!=set(entries):raise ValueError('unwitnessed context indexes; do not guess scopes or discard caches')
    return {'version':1,'old_runtime':str(old),'new_runtime':str(new),'index_changes':moves,
            'requires_session_byte_preservation':True,'requires_record_store_byte_preservation':True,
            'production_apply_supported':False,'reasoning_bodies_read':False}
