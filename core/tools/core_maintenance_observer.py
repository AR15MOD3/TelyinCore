"""Offline maintenance primitives; no HTTP, discovery, service or PID controls."""
import json,os,subprocess,time,math
from pathlib import Path

def _publish(path,value):
    path=Path(path)
    raw=json.dumps(value,ensure_ascii=False,indent=2).encode()
    # Journal path belongs exclusively to one invocation; never reuse an old path.
    temp=path.with_name(path.name+'.tmp')
    with temp.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)

def observe_chain(retained,journal,*,timeout=30,clock=time.monotonic):
    """Record raw results BEFORE classifying; no kill/restart on any outcome.

    retained supplies birth-bound wait handles, never discovered PID authority.
    All handles observed under one total deadline, including after an error.
    A nonzero code remains a failure; 130 is requested cancellation, not flush.
    """
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<=timeout<=300:raise ValueError('finite bounded timeout required')
    path=Path(journal)
    with path.open('xb') as f:f.write(b'{"state":"observing","rows":[]}');f.flush();os.fsync(f.fileno())
    end=clock()+timeout;rows=[]
    for handle in retained:
        row=dict(pid=handle.pid,birth=handle.birth)
        try:
            code=handle.wait(max(0,end-clock()))
            row.update(exit_code=code,observed=code is not None)
            row['classification']=('timeout' if code is None else 'normal' if code==0 else 'requested_cancellation' if code==130 else 'abnormal')
        except BaseException as error:
            row.update(exit_code=None,observed=False,error_type=type(error).__name__,classification='observation_failed')
            rows.append(row);_publish(path,dict(state='interrupted' if not isinstance(error,Exception) else 'observing',rows=rows))
            if not isinstance(error,Exception):raise
            continue
        rows.append(row);_publish(path,dict(state='observing',rows=rows))
    clean=all(x.get('observed') and x.get('exit_code') in (0,130) for x in rows) and bool(rows)
    result=dict(state='observed',accepted_codes=clean,all_exit_codes_observed=bool(rows) and all(x.get('observed') for x in rows),rows=rows,force_kills=0,restarts=0)
    _publish(path,result)
    return result

def start_hidden(argv,*,cwd,stdout_path,stderr_path):
    """Return original Popen immediately. No PowerShell or output pipes.

    Caller retains the process and owns separate readiness/deadline checks.
    Timeout in a monitor never automatically terminates/restarts this launcher.
    Raw logs can contain application data; caller must not ingest them blindly.
    """
    outpath,errpath=Path(stdout_path),Path(stderr_path)
    if outpath.resolve()==errpath.resolve():raise ValueError('separate output paths required')
    with outpath.open('xb') as out,errpath.open('xb') as err:
        return subprocess.Popen(list(argv),cwd=cwd,stdin=subprocess.DEVNULL,
            stdout=out,stderr=err,close_fds=True,shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
