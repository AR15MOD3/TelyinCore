"""Rebase a moved Linux profile before its normal ownership check."""
import base64
import csv
import hashlib
import io
import json
import os
import shlex
from pathlib import Path

from bot_configured_account import _digest_path
from bot_guarded_store import GuardedStore
from bot_migration_gate import assert_no_migration
from bot_plugin_ownership import _account
from bot_registry import bot_id
from deployment_relocation import relocate_deliveries
from relocation_files import install_bytes, write_json
from runtime_identity import identity_lock, safe_path
from telyincore.source_guard import lease


def interpreter_script(raw, old_bin, new_bin):
    lines = raw.split(b'\n', 3)
    if not lines or not lines[0].startswith(b'#!'):
        return
    if lines[0] == b'#!/bin/sh' and len(lines) == 4 and lines[2] == b"' '''" and lines[1].startswith(b"'''exec' "):
        command = lines[1][9:]
        if not command.endswith(b' "$0" "$@"'):
            return
        try:
            argv = shlex.split(os.fsdecode(command[:-10]))
        except ValueError:
            return
        if len(argv) != 1:
            return
        previous = Path(argv[0])
        body = lines[3]
    else:
        previous = Path(os.fsdecode(lines[0][2:]))
        body = raw.partition(b'\n')[2]
    if previous.parent != old_bin or previous.name not in ('python','python3',new_bin.joinpath('python').resolve().name):
        return
    python = new_bin / previous.name
    if not python.is_file():
        raise ValueError('移动后的 Python 解释器不存在')
    simple = b'#!' + os.fsencode(python)
    if b' ' not in simple and b'\t' not in simple and len(simple) < 127:
        return simple + b'\n' + body
    return b"#!/bin/sh\n'''exec' " + os.fsencode(shlex.quote(str(python))) + b' "$0" "$@"\n\' \'\'\'\n' + body


def repair_environment(core, old_core):
    root = core.parent
    env = core/'.venv-linux'
    new_bin = env/'bin'
    old_bin = old_core/'.venv-linux/bin'
    if not new_bin.is_dir():
        return
    for path in new_bin.iterdir():
        if path.is_symlink() or not path.is_file() or path.stat().st_size>2*1024*1024:
            continue
        raw = path.read_bytes()
        revised = interpreter_script(raw,old_bin,new_bin)
        if revised is None and path.name in ('activate','activate.csh','activate.fish'):
            revised = raw.replace(os.fsencode(old_bin.parent),os.fsencode(new_bin.parent))
        if revised is not None and revised != raw:
            install_bytes(root,path,raw,revised)
    for record in env.glob('lib/python*/site-packages/*.dist-info/RECORD'):
        raw = record.read_bytes()
        rows = list(csv.reader(io.StringIO(raw.decode(),newline='')))
        changed = False
        for row in rows:
            if len(row)!=3 or not row[1].startswith('sha256='):continue
            path = (record.parent.parent/row[0]).resolve()
            if path.parent!=new_bin or not path.is_file():continue
            digest = base64.urlsafe_b64decode(row[1][7:]+'==').hex()
            before = safe_path(root, root/'runtime/relocation-backups/files'/path.relative_to(root)/digest)
            if not before.is_file():continue
            previous = before.read_bytes()
            actual = path.read_bytes()
            if actual==previous:continue
            if hashlib.sha256(previous).hexdigest()!=digest or interpreter_script(previous,old_bin,new_bin)!=actual:
                raise ValueError('Linux 命令入口与原安装记录不符')
            row[1]='sha256='+base64.urlsafe_b64encode(hashlib.sha256(actual).digest()).decode().rstrip('=')
            row[2]=str(len(actual))
            changed=True
        if changed:
            text=io.StringIO(newline='')
            csv.writer(text,lineterminator='\r\n' if b'\r\n' in raw else '\n').writerows(rows)
            install_bytes(root,record,raw,text.getvalue().encode())


def repair_menu(core, old_core):
    from scripts.linux_shortcut import exec_arg, write_entry
    folder = Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')/'applications'
    old = exec_arg(old_core.parent/'启动 Telyincore.sh')
    new = exec_arg(core.parent/'启动 Telyincore.sh')
    for name in ('telyincore-linux.desktop','telyincore-qq.desktop'):
        path = folder/name
        if path.is_symlink() or not path.is_file():continue
        lines = path.read_text().splitlines(keepends=True)
        for i,line in enumerate(lines):
            if line.startswith('Exec=') and old in line:
                lines[i]=line.replace(old,new)
                archive=core.parent/'runtime/relocation-backups/menu'/hashlib.sha256(line.encode()).hexdigest()
                archive.mkdir(parents=True,exist_ok=True)
                if not (archive/name).exists():write_entry(archive,name,path.read_text())
                write_entry(folder,name,''.join(lines))
                break


def repair_profile(core, workspace, old_workspace, bot):
    data=workspace/'core/data'
    if GuardedStore(workspace,data/'bot-runtime-owner.json').read()!={'baseline':'AcceptanceBaseline'}:
        raise ValueError('Linux 档案运行记录归属不匹配')
    home=safe_path(workspace,workspace/'bots'/bot)
    if not home.is_dir():
        raise ValueError('移动后的 Bot 档案缺失')
    store=home/'infinity/processes/snowluma-recovery.json'
    with identity_lock(data):
        account=GuardedStore(workspace,store).read().get('targetUin')
        if account:
            account=_account(account)
            key=hashlib.sha256(b'configured-qq-account-v1\0'+account.encode()).hexdigest()
            path=data/'qq-configured-account-reservations'/(key+'.json')
            if path.with_suffix('.pending').exists():raise ValueError('QQ 账号预留尚未完成')
            disk=GuardedStore(workspace,path)
            def expected(at):
                return dict(version=1,account=key,scope=_digest_path(at/data.relative_to(workspace)),
                            store=_digest_path(at/store.relative_to(workspace)))
            if disk.raw is not None and disk.read()!=expected(workspace):
                if disk.read()!=expected(old_workspace):raise ValueError('QQ 账号预留与原档案不匹配')
                others=[data/'infinity-processes/snowluma-recovery.json',*(workspace/'bots').glob('*/infinity/processes/snowluma-recovery.json')]
                if any(p!=store and GuardedStore(workspace,p).read().get('targetUin')==account for p in others):
                    raise ValueError('QQ 账号同时被另一档案配置')
                write_json(core.parent,path,disk.raw,expected(workspace))
        cfg=home/'infinity/qq/config.json'
        disk=GuardedStore(workspace,cfg)
        settings=disk.read()
        model=Path(settings.get('asrModelDir') or '.')
        old_core=old_workspace.parent
        if model.parent==old_core/'models/voice/asr':
            moved=safe_path(core,core/'models/voice/asr'/model.name)
            if not all((moved/n).is_file() for n in ('tokens.txt','model.int8.onnx')):
                raise ValueError('移动后的语音模型不完整')
            write_json(core.parent,cfg,disk.raw,dict(settings,asrModelDir=str(moved)))
    memory=home/'memory'
    if any((memory/n/'pending.json').exists() for n in ('ingress-delivery','local-observation-delivery')):
        from telyincore.events import EventStore
        relocate_deliveries(workspace/'core',bot,memory,EventStore(data/'events',read_only=True),
                            previous=old_workspace,linux_profile=True)


def reconcile_marker(core, workspace, marker, expected):
    with lease(marker):
        disk=GuardedStore(core.parent,marker)
        actual=disk.read()
        if actual==expected:return
        old_core=Path(actual.get('source_core') or '.')
        if (not old_core.is_absolute() or '..' in old_core.parts or old_core.name!='core'
                or actual!=dict(expected,source_core=str(old_core)) or (old_core/'launch.py').exists()):
            raise ValueError('原 Linux 安装仍存在或档案归属不匹配')
        assert_no_migration(workspace/'core/data')
        if expected.get('mode')=='linux-profile-acceptance':
            repair_profile(core,workspace,old_core/workspace.name,bot_id(expected['bot']))
        repair_environment(core,old_core)
        repair_menu(core,old_core)
        from relocation_voice import repair_voice
        repair_voice(core,old_core.parent)
        write_json(core.parent,marker,disk.raw,expected)

def check_environment(core):
    """Virtualenv launchers can retain their creation path even in a copied deployment."""
    root=core.parent
    path=safe_path(root,root/'runtime/relocation-check/linux-environment.json')
    with lease(path):
        disk=GuardedStore(root,path)
        if disk.raw is not None:
            row=disk.read()
            if set(row)!={'version','core'} or row['version']!=1:
                raise ValueError('Linux Python 环境来源记录无效')
            old=Path(row['core'])
        else:
            cfg=core/'.venv-linux/pyvenv.cfg'
            if not cfg.is_file():return
            line=next((x for x in cfg.read_text().splitlines() if x.startswith('command = ')), '')
            previous=line.partition(' -m venv ')[2].strip().strip('"')
            if not previous or Path(previous).name!='.venv-linux':return
            old=Path(previous).parent
        if not old.is_absolute() or '..' in old.parts or old.name!='core':
            raise ValueError('Linux Python 环境原路径无效')
        if old!=core:
            repair_environment(core,old)
        if disk.raw is None or old!=core:
            disk.write(dict(version=1,core=str(core)))
