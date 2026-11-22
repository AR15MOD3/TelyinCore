"""Finite local storage observer; never launches a Bot or calls a model."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import time

CORE=Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:sys.path.insert(0,str(CORE))
from telyincore.storage_watch import scan,run_once,atomic,state_directory


def windows_once(root):
    # Own only this short-lived observer tree. No discovered process authority.
    import ctypes as C
    from telyincore.runtime_evidence_supervisor import Job,ExtendedLimits
    job=Job();limits=ExtendedLimits();limits.basic.flags=0x2000|0x200|0x8|0x10
    limits.basic.active_limit=8;limits.basic.affinity=1;limits.memory[1]=256*1024**2
    job.check(job.k.SetInformationJobObject(job.handle,9,C.byref(limits),C.sizeof(limits)))
    bootstrap="import sys; assert sys.stdin.readline()=='GO'+chr(10); import runpy; script=sys.argv.pop(1); runpy.run_path(script,run_name='__main__')"
    child=None;timed_out=False;raw=b''
    try:
        child=subprocess.Popen([sys.executable,'-B','-c',bootstrap,str(Path(__file__).resolve()),'--worker','--root',str(root)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1'))
        job.assign(child)
        try:raw,_=child.communicate(b'GO\n',timeout=45)
        except subprocess.TimeoutExpired:
            timed_out=True;job.terminate();raw,_=child.communicate(timeout=10)
        end=time.monotonic()+5
        while job.state()['active'] and time.monotonic()<end:time.sleep(.02)
        result=dict(exit_code=child.returncode,timed_out=timed_out,job=job.state())
        if result['job']['active']:
            job.terminate();end=time.monotonic()+5
            while job.state()['active'] and time.monotonic()<end:time.sleep(.02)
            result['job']=job.state();result['forced_cleanup']=True
        atomic(state_directory(root),'last-run.json',json.dumps(result).encode())
        print(json.dumps(result))
        if child.returncode!=0 or timed_out or result.get('forced_cleanup'):
            print(raw.decode('utf-8',errors='replace')[-4000:],file=sys.stderr)
            return 1
        return 0
    finally:
        job.close()
        if child is not None and child.poll() is None:
            child.terminate();child.wait(timeout=10)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=CORE.parent)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check',action='store_true',help='read-only metadata report; no notifications or state writes')
    mode.add_argument('--once',action='store_true',help='publish local status and request due desktop alert')
    mode.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.check:
        print(json.dumps(scan(args.root),ensure_ascii=False,indent=2));return 0
    if os.name=='nt' and not args.worker:return windows_once(args.root)
    report,state=run_once(args.root)
    print(json.dumps(dict(complete=report['complete'],history_bytes=report['history_bytes'],
                         alerts=len(report['alerts']),notification_requested=state['notification_requested'],
                         delivery_confirmed=False,originals_modified=False,writes_blocked=False)))
    return 0


if __name__=='__main__':raise SystemExit(main())
