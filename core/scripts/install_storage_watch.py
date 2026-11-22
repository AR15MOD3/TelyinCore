"""Install only a local storage-observer schedule, independent of Core lifecycle.

No Bot startup, credentials, network destinations, history mutation or quotas.
Windows uses the current user's interactive token; logged-out desktop delivery
is not promised. Source: https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

CORE=Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:sys.path.insert(0,str(CORE))
from telyincore.storage_watch import plain,state_directory,atomic,SCHEMA


def identity(root):
    # Stable per installation; independent installations never replace each other.
    value=os.path.normcase(str(Path(root).absolute()))
    return hashlib.sha256(os.fsencode(value)).hexdigest()[:16]


def systemd_quote(value):
    value=str(value)
    if any(c in value for c in '\r\n\0'):raise ValueError('invalid unit path')
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'


def linux_plan(root, python):
    root=Path(root).absolute();name='telyincore-storage-watch-'+identity(root)
    argv=[str(python),'-B',str(root/'core/tools/storage_watch.py'),'--once']
    service=('# '+SCHEMA+'\n[Unit]\nDescription=TelyinCore local storage advisory\n'
             '[Service]\nType=oneshot\nExecStart='+ ' '.join(systemd_quote(v) for v in argv)+'\n'
             'Environment=PYTHONDONTWRITEBYTECODE=1\nMemoryMax=256M\nMemorySwapMax=0\n'
             'CPUQuota=20%\nTasksMax=16\nRuntimeMaxSec=60s\nUMask=0077\n')
    timer=('# '+SCHEMA+'\n[Unit]\nDescription=TelyinCore local storage advisory schedule\n'
           '[Timer]\nOnBootSec=5min\nOnUnitActiveSec=15min\nAccuracySec=30s\nUnit='+name+'.service\n'
           '[Install]\nWantedBy=timers.target\n')
    return name,service,timer


def windows_plan(root, python):
    root=Path(root).absolute()
    name='TelyinCoreStorageWatch-'+identity(root)
    argv=[str(python),'-B',str(root/'core/tools/storage_watch.py'),'--once']
    action=subprocess.list2cmdline(argv)
    # Deliberately no /F, /RU, password or SYSTEM. Refuse an existing foreign task.
    return name,action,['schtasks.exe','/Create','/TN',name,'/TR',action,'/SC','MINUTE','/MO','15','/IT','/RL','LIMITED']


def decode_task_xml(data, *, encoding=None):
    """schtasks can declare UTF-16 while writing the console code page.

    BOM/UTF-16 byte markers take precedence. For unmarked CLI bytes use the
    actual Windows console/OEM code page, never lossy replacement. Remove only
    the optional declaration after decoding; all identity/action text remains.
    """
    if len(data)>1024*1024:raise ValueError('task XML size limit')
    if isinstance(data,bytes):
        if data.startswith((b'\xff\xfe',b'\xfe\xff')):codec='utf-16'
        elif data.startswith(b'<\x00'):codec='utf-16-le'
        elif data.startswith(b'\x00<'):codec='utf-16-be'
        elif data.startswith(b'\xef\xbb\xbf'):codec='utf-8-sig'
        else:
            codec=encoding
            if codec is None and os.name=='nt':
                import ctypes
                kernel=ctypes.WinDLL('kernel32')
                codepage=kernel.GetConsoleOutputCP() or kernel.GetOEMCP()
                codec='cp'+str(codepage)
            codec=codec or 'utf-8'
        data=data.decode(codec,errors='strict')
    if not isinstance(data,str):raise ValueError('task XML text required')
    return re.sub(r'^\ufeff?\s*<\?xml[^?]*\?>','',data,count=1)


def matches_task(xml, python, script, *, user_id=None):
    doc=ET.fromstring(decode_task_xml(xml));ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
    commands=doc.findall('./t:Actions/t:Exec',ns)
    if len(commands)!=1:return False
    action=commands[0];command=action.findtext('t:Command',default='',namespaces=ns).strip('"')
    arguments=action.findtext('t:Arguments',default='',namespaces=ns)
    token=doc.findtext('./t:Principals/t:Principal/t:LogonType',default='',namespaces=ns)
    owner=doc.findtext('./t:Principals/t:Principal/t:UserId',default='',namespaces=ns)
    intervals=doc.findall('./t:Triggers/t:CalendarTrigger/t:Repetition/t:Interval',ns)+doc.findall('./t:Triggers/t:TimeTrigger/t:Repetition/t:Interval',ns)
    return (os.path.normcase(command)==os.path.normcase(str(python))
            and arguments==subprocess.list2cmdline(['-B',str(script),'--once'])
            and token=='InteractiveToken' and (user_id is None or owner.casefold()==user_id.casefold()) and any(x.text=='PT15M' for x in intervals))


def install(root, python, *, run=subprocess.run, user_units=None):
    root=Path(root).absolute();plain(root);plain(root/'core');plain(root/'core/tools');plain(root/'core/tools/storage_watch.py')
    if not Path(python).is_file():raise ValueError('installed Python required')
    if os.name=='nt':
        identity_result=run(['whoami.exe','/user','/fo','csv','/nh'],capture_output=True,timeout=10)
        ids=re.findall(r'S-1-(?:[0-9]+-)+[0-9]+',identity_result.stdout.replace(b'\0',b'').decode('ascii',errors='ignore'))
        if identity_result.returncode or len(ids)!=1:raise RuntimeError('current user SID unavailable')
        user_id=ids[0]
        name,action,command=windows_plan(root,python)
        before=run(['schtasks.exe','/Query','/TN',name,'/XML'],capture_output=True)
        if before.returncode==0:
            if not matches_task(before.stdout,python,root/'core/tools/storage_watch.py',user_id=user_id):
                raise ValueError('existing task differs; preserved')
        else:
            result=run(command,capture_output=True)
            if result.returncode:raise RuntimeError('local storage schedule creation failed')
        after=run(['schtasks.exe','/Query','/TN',name,'/XML'],capture_output=True)
        if after.returncode or not matches_task(after.stdout,python,root/'core/tools/storage_watch.py',user_id=user_id):
            raise RuntimeError('storage task verification failed')
        result={'schema':SCHEMA,'scheduler':'windows-task','name':name,'interval_minutes':15,'interactive_only':True,'verified':True}
    else:
        name,service,timer=linux_plan(root,python)
        folder=Path(user_units) if user_units is not None else Path.home()/'.config/systemd/user'
        folder.mkdir(parents=True,exist_ok=True);plain(folder)
        pairs=[(name+'.service',service),(name+'.timer',timer)]
        for filename,content in pairs:
            path=folder/filename
            if os.path.lexists(path) and (not stat_regular(path) or path.read_text()!=content):
                raise ValueError('existing user unit differs; preserved')
        for filename,content in pairs:
            path=folder/filename
            if not os.path.lexists(path):atomic(folder,filename,content.encode())
        run(['systemctl','--user','daemon-reload'],check=True,capture_output=True)
        run(['systemctl','--user','enable','--now',name+'.timer'],check=True,capture_output=True)
        run(['systemctl','--user','is-enabled',name+'.timer'],check=True,capture_output=True)
        run(['systemctl','--user','is-active',name+'.timer'],check=True,capture_output=True)
        result={'schema':SCHEMA,'scheduler':'systemd-user','name':name,'interval_minutes':15,'verified':True}
    atomic(state_directory(root),'schedule.json',json.dumps(result,indent=2).encode())
    return result


def stat_regular(path):
    import stat
    return stat.S_ISREG(plain(path).st_mode)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--install',action='store_true');args=parser.parse_args(argv)
    root=CORE.parent
    if not args.install:
        plan=windows_plan(root,sys.executable) if os.name=='nt' else linux_plan(root,sys.executable)
        print(json.dumps({'plan':plan,'installed':False},ensure_ascii=False,indent=2));return 0
    print(json.dumps(install(root,sys.executable),ensure_ascii=False));return 0


if __name__=='__main__':raise SystemExit(main())
