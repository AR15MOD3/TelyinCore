"""Explicit library runner for six synthetic cache attempts; no CLI entry point.

Calling run_budgeted requires a caller-owned output directory and sample callback.
Import alone does not execute samples; no implicit --execute option is provided.
"""
import asyncio,hashlib,json,os,shutil,time
from pathlib import Path
from .cache_retrieval_control import digest

LIMIT=6

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        backup=path.parent/'backups';backup.mkdir(exist_ok=True)
        shutil.copy2(path,backup/(path.name+'.'+str(time.time_ns())))
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)

async def run_budgeted(output,plans,sample,*,gap_seconds=3):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if len(plans)!=LIMIT:raise ValueError('exactly six locked samples required')
    signature=digest(plans);path=output/'checkpoint.json';lock=output/'.run.lock'
    # A crashed process leaves a pending checkpoint or lock. No PID adoption,
    # automatic stale-lock deletion, budget reset, or uncertain-request retry.
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    try:
        if path.exists():
            state=json.loads(path.read_text(encoding='utf-8'))
            if state.get('plan_hash')!=signature or state.get('limit')!=LIMIT:
                raise ValueError('plan or budget drift')
            if type(state.get('consumed')) is not int or not 0<=state['consumed']<=LIMIT:
                raise ValueError('invalid persisted budget')
            if state.get('pending') is not None:
                state['status']='uncertain';atomic_json(path,state);return state
            if state['status']!='active':return state
        else:
            state={'version':1,'plan_hash':signature,'limit':LIMIT,'consumed':0,
                   'pending':None,'status':'active','rows':[]}
            atomic_json(path,state)
        for index in range(len(state['rows']),len(plans)):
            if state['consumed']>=LIMIT:
                state['status']='budget_exhausted';atomic_json(path,state);return state
            state['consumed']+=1;state['pending']=index
            atomic_json(path,state)  # fsync budget BEFORE invoking any sample
            try:
                row=await sample(plans[index],index)
            except Exception as exc:
                row={'arm':plans[index]['arm'],'stage':plans[index]['stage'],
                     'error_type':type(exc).__name__,'behavior_pass':False,'wire_hash_matches':False}
            # CancelledError/BaseException intentionally leave pending charged.
            state['rows'].append(row);state['pending']=None
            if not (row.get('behavior_pass') and row.get('wire_hash_matches')):
                state['status']='failed';atomic_json(path,state);return state
            if len(state['rows'])==len(plans):state['status']='complete'
            atomic_json(path,state)
            if state['status']=='active' and gap_seconds:await asyncio.sleep(gap_seconds)
        return state
    finally:lock.unlink(missing_ok=True)
