"""Reconcile known path-bound resources after moving a Windows installation."""
import argparse
import base64
import csv
import io
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

from bot_configured_account import _digest_path
from bot_guarded_store import GuardedStore
from bot_migration_gate import assert_identity_allowed
from bot_plugin_ownership import _account
from relocation_files import install_bytes, write_json
from relocation_voice import repair_voice
from bot_registry import bot_id
from deployment_relocation import previous_installation, relocate_deliveries, verify_bot
from runtime_identity import identity_lock, safe_path
from telyincore.source_guard import lease


def repair_profile(core, old, bot):
    root = core.parent
    base = core/'data'
    assert_identity_allowed(base, bot)
    home = root/'bots'/bot
    from service_layout import qq_recovery_file
    manifest = GuardedStore(root, base/'bot-runtime-owner.json').read()
    baseline = manifest.get('baseline')
    store = qq_recovery_file(core, bot, baseline=bot==baseline)
    memory = home/'memory'
    dirs = ('ingress-delivery','local-observation-delivery')
    cfg = home/'infinity/qq/config.json'
    pending = any((memory/name/'pending.json').is_file() for name in dirs)
    if not (store.is_file() or cfg.is_file() or pending):
        return 0
    events = verify_bot(core, bot)/'events'
    count = 0
    with identity_lock(base):
        data = GuardedStore(root, store).read() if store.is_file() else {}
        if data.get('targetUin'):
            account = _account(data['targetUin'])
            key = hashlib.sha256(b'configured-qq-account-v1\0'+account.encode()).hexdigest()
            path = base/'qq-configured-account-reservations'/(key+'.json')
            if path.with_suffix('.pending').exists():
                raise ValueError('账号预留有未完成的写入')
            if path.exists():
                disk = GuardedStore(root, path)
                row = disk.read()
                def expected(at):
                    return dict(version=1, account=key, scope=_digest_path(at/base.relative_to(root)),
                                store=_digest_path(at/store.relative_to(root)))
                if row != expected(root):
                    if type(row.get('version')) is not int or row != expected(old):
                        raise ValueError('账号预留与原安装目录的 Bot 不匹配')
                    others = [base/'infinity-processes/snowluma-recovery.json',
                              *(root/'bots').glob('*/infinity/processes/snowluma-recovery.json')]
                    if any(p!=store and p.is_file() and str(GuardedStore(root,p).read().get('targetUin'))==account for p in others):
                        raise ValueError('同一账号被多个 Bot 配置')
                    write_json(root,path,disk.raw,expected(root))
                    count += 1
        if cfg.is_file():
            disk = GuardedStore(root,cfg)
            settings = disk.read()
            model = Path(settings.get('asrModelDir') or '.')
            source = old/'core/models/voice/asr'
            if model.parent==source:
                new = safe_path(root,core/'models/voice/asr'/model.name)
                if not all((new/name).is_file() for name in ('tokens.txt','model.int8.onnx')):
                    raise ValueError('移动后的 ASR 模型不完整')
                write_json(root,cfg,disk.raw,dict(settings,asrModelDir=str(new)))
                count += 1
    if pending:
        from telyincore.events import EventStore
        reader = EventStore(events,read_only=True)
        count += len(relocate_deliveries(core, bot, memory, reader, previous=old))
    return count


def rebase_entrypoint(raw, origins, python):
    # A Windows wheel entry point has a PE stub, the interpreter shebang
    # and an appended zip. Preserve both executable code and zip contents.
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.namelist()!=['__main__.py']:
            return None
        offset = archive.infolist()[0].header_offset
    match = re.search(rb'#!("[^"\r\n]+"|[^\r\n]+)\r?\n(?:\r\n)?\Z', raw[:offset])
    if not match or Path(os.fsdecode(match[1].strip(b'"'))) not in origins:
        return None
    wire = os.fsencode(str(python))
    if b' ' in wire:
        wire = b'"'+wire+b'"'
    return raw[:match.start(1)]+wire+raw[match.end(1):]


def repair_wheel_records(root, folder, origins, python):
    site = folder/'Lib/site-packages'
    for record in site.glob('*.dist-info/RECORD'):
        raw = record.read_bytes()
        rows = list(csv.reader(io.StringIO(raw.decode('utf8'),newline='')))
        changed = False
        for row in rows:
            if len(row)!=3 or not row[0].lower().endswith('.exe') or not row[1].startswith('sha256='):
                continue
            path = (site/row[0]).resolve()
            if path.parent!=python.parent or not path.is_file():
                continue
            digest = base64.urlsafe_b64decode(row[1][7:]+'==').hex()
            archive = safe_path(root,root/'runtime/relocation-backups/files'/path.relative_to(root)/digest)
            if not archive.exists():
                continue
            before = archive.read_bytes()
            if hashlib.sha256(before).hexdigest()!=digest:
                raise ValueError('命令入口备份校验失败')
            installed = path.read_bytes()
            if installed==before:
                continue
            replacement = rebase_entrypoint(before,origins,python)
            if replacement is None or replacement!=installed:
                raise ValueError('命令入口与原安装记录不匹配')
            row[1] = 'sha256='+base64.urlsafe_b64encode(hashlib.sha256(replacement).digest()).decode().rstrip('=')
            row[2] = str(len(replacement))
            changed = True
        if changed:
            output = io.StringIO(newline='')
            csv.writer(output,lineterminator='\r\n' if b'\r\n' in raw else '\n').writerows(rows)
            install_bytes(root,record,raw,output.getvalue().encode('utf8'))


def repair_scripts(core, old):
    root = core.parent
    count = 0
    # The old CosyVoice installer used a staging name for its environment.
    for folder in (core/'.venv', root/'services/CosyVoice3/.venv'):
        scripts = safe_path(root,folder/'Scripts')
        python = scripts/'python.exe'
        if not python.is_file():
            continue
        old_env = old/folder.relative_to(root)
        origins = [old_env/'Scripts/python.exe']
        if folder != core/'.venv':
            origins.append(old/'services/.cosy-runtime-05320/.venv/Scripts/python.exe')
        for path in scripts.glob('*.exe'):
            if path.stat().st_size>4*1024*1024 or not zipfile.is_zipfile(path):
                continue
            raw = path.read_bytes()
            replacement = rebase_entrypoint(raw, origins, python)
            if replacement is not None:
                install_bytes(root,path,raw,replacement)
                count += 1
        repair_wheel_records(root,folder,origins,python)
        for name in ('activate','activate.bat','Activate.ps1','activate.fish'):
            path = scripts/name
            if not path.is_file():continue
            raw = path.read_bytes()
            revised = raw.replace(str(old_env).encode(),str(folder).encode())
            if revised != raw:
                install_bytes(root,path,raw,revised)
                count += 1
    return count


def reconcile(core, bot, *, strict=False, rescan=False):
    """Use a separate last-successful root for shared resources and each Bot."""
    root = core.parent
    bot = bot_id(bot)
    folders = safe_path(root,root/'runtime/relocation-check')
    results = {}
    operations = {'python_scripts':repair_scripts, 'scheduled_tasks':repair_tasks, 'voice_models':repair_voice,
                  'bot-'+bot:lambda core,old:repair_profile(core,old,bot)}
    errors = []
    for key, repair in operations.items():
        path = folders/(key+'.json')
        try:
            with lease(path,timeout=15):
                state = GuardedStore(root,path)
                if state.raw is not None:
                    fields = state.read()
                    if (set(fields) not in ({'version','root'}, {'version','root','previous'})
                            or type(fields['version']) is not int or fields['version']!=1):
                        raise ValueError('迁移来源登记无效')
                    old = Path(fields['root'])
                    if old == root and rescan:
                        old = Path(fields['previous']) if fields.get('previous') else previous_installation(core)
                else:
                    old = previous_installation(core)
                if old==root:continue
                if old is not None:
                    if not old.is_absolute() or '..' in old.parts or str(old).startswith(('\\\\','//')) or (old/'core/launch.py').exists():
                        raise ValueError('原部署仍存在或无法核实')
                    results[key]=repair(core,old)
                manifest = dict(version=1,root=str(root),previous=str(old) if old else None)
                if state.read()!=manifest:
                    state.write(manifest)
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
            errors.append(f'{key}：{error}')
    if errors:
        message = '迁移自检有待处理项目：'+'；'.join(errors)
        if strict:raise RuntimeError(message)
        print(message,file=sys.stderr,flush=True)
    return results


def repair_tasks(core, old):
    script = core/'scripts/relocate_tasks.ps1'
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                             '-File',str(script),str(old),str(core.parent)],capture_output=True,timeout=90)
    if result.returncode:
        raise ValueError('计划任务路径核验失败：'+result.stderr.decode(errors='replace')[-500:])
    return int(result.stdout.strip())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bot',required=True)
    parser.add_argument('--rescan',action='store_true',help='重新核对上一次目录迁移')
    args=parser.parse_args()
    print(json.dumps(reconcile(Path(__file__).resolve().parent,args.bot,strict=True,rescan=args.rescan)))
