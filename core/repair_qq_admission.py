"""Offline Windows recovery for an idle, unretired v1 local QQ admission."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from xml.etree import ElementTree
import uuid

from bot_default_account_fence import DefaultAccountFence
from bot_login_lifecycle import LoginLifecycle
from bot_migration_gate import assert_identity_allowed
from bot_parent_account_grant import ParentAccountGrant
from bot_private_file import open_private_new
from bot_recovery_http_lease import RecoveryClientLease
from bot_recovery_mutation_journal import MutationJournal
from bot_registry import bot_id
from bot_stable_file import read_stable
from bot_startup_probe import _json
from runtime_identity import identity_lock, safe_path


def windows_session(core):
    """Pin one local Windows installation and one kernel boot, never wall time."""
    import winreg
    from ctypes import wintypes
    if str(core).startswith(('\\\\', '//')):
        raise ValueError('请在原电脑的本地安装目录运行')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumePathNameW.restype = wintypes.BOOL
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    volume = ctypes.create_unicode_buffer(32768)
    if not kernel.GetVolumePathNameW(str(core), volume, len(volume)) or kernel.GetDriveTypeW(volume.value) != 3:
        raise ValueError('需要本地固定磁盘')
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                        access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        machine = uuid.UUID(winreg.QueryValueEx(key, 'MachineGuid')[0])
    # Cross-check the local boot counter with the kernel's startup event.
    # Log clearing, a missing counter or an unreadable log must not release a grant.
    counter_key = r'SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters'
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, counter_key) as key:
        counter, kind = winreg.QueryValueEx(key, 'BootId')
    if kind != winreg.REG_DWORD or not 0 < counter <= 0xffffffff or not machine.int:
        raise ValueError('Windows 启动计数无效')
    system_dir = ctypes.create_unicode_buffer(32768)
    if not 0 < kernel.GetSystemDirectoryW(system_dir, len(system_dir)) < len(system_dir):
        raise ValueError('无法定位 Windows 事件日志工具')
    selector = "*[System[Provider[@Name='Microsoft-Windows-Kernel-General'] and EventID=12]]"
    try:
        result = subprocess.run([str(Path(system_dir.value)/'wevtutil.exe'), 'qe', 'System',
                                 '/q:'+selector, '/rd:true', '/c:1', '/f:xml', '/uni:true'],
                                capture_output=True, timeout=10, check=True)
        event = ElementTree.fromstring(result.stdout)
        ns = {'e':'http://schemas.microsoft.com/win/2004/08/events/event'}
        system = event.find('e:System', ns)
        if (system is None or system.findtext('e:EventID', namespaces=ns) != '12'
                or system.findtext('e:Channel', namespaces=ns) != 'System'):
            raise ValueError('缺少 Windows 内核启动事件')
        record = int(system.findtext('e:EventRecordID', namespaces=ns) or 0)
        if record <= 0:
            raise ValueError('Windows 启动事件序号无效')
    except (ElementTree.ParseError, subprocess.SubprocessError) as error:
        raise ValueError('Windows 内核启动日志不可核验') from error
    return hashlib.sha256(machine.bytes).hexdigest(), [counter, record]


def check_idle(grant, admission):
    base, root, store = grant.base, grant.core.parent, grant.store
    assert_identity_allowed(base, grant.bot)
    DefaultAccountFence(grant.core, grant.bot).check()
    checks = (
        (admission.with_suffix('.work.json'), '有未退役的账号操作'),
        (base/'instances'/(grant.bot + '.json'), '当前 Bot 的实例登记仍在'),
        (base/'snowluma-companion-custody.json', '共享托管登记仍在'),
        (root/'bots'/grant.bot/'infinity/snowluma/companion-custody.json', '当前 Bot 的托管登记仍在'),
    )
    for path, reason in checks:
        if safe_path(root, path).exists():
            raise ValueError(reason)
    if any(store.parent.glob(store.name + '.*.pending')):
        raise ValueError('账号设置仍有未提交文件')
    RecoveryClientLease.check(root, store)
    LoginLifecycle(root, store).check(lambda: None)
    if MutationJournal(root, store)._records():
        raise ValueError('有待核对的 QQ 操作记录')


def retirement_evidence(root, admission, nonce, digest, directory, history):
    """A terminal receipt may already have moved before the original HOST died."""
    original = safe_path(root, admission.with_suffix('.local-retired.json'))
    staged = safe_path(root, directory/'local-retired.json')
    history_receipt = safe_path(root, history/'admission.local-retired.json')
    if history.exists() and set(history.iterdir()) != {history_receipt}:
        raise ValueError('原退役归档不完整或含有未知文件')
    receipts = [p for p in (original, staged, history_receipt) if p.exists()]
    if not receipts:
        return None, None
    if len(receipts) != 1:
        raise ValueError('发现多份退役回执')
    receipt = receipts[0]
    raw, file_id = read_stable(root, receipt, 1024)
    row = _json(raw)
    expected = dict(version=1, grant=nonce, admission_sha256=digest,
                    local_resources_drained=True, external_account_offline=False)
    if (row != expected or type(row.get('version')) is not int
            or row.get('local_resources_drained') is not True or row.get('external_account_offline') is not False):
        raise ValueError('本地退役回执与原准入登记不符')
    return receipt, dict(sha256=hashlib.sha256(raw).hexdigest(), file_id=list(file_id))


def revision_state(root, active, archive_dir, row):
    from qq_startup_diagnostics import revisions
    binding = {key:row[key] for key in ('account','bot','store','owner')}
    live = revisions(root, active, binding)
    moved = revisions(root, archive_dir, binding)
    if (set(live) & set(moved) or any(n.endswith('.pending') for n in (*live,*moved))
            or archive_dir.exists() and {p.name for p in archive_dir.iterdir()} != moved.keys()):
        raise ValueError('凭据修订尚未完成或归档不一致')
    all_rows = {**live, **moved}
    digest = hashlib.sha256(json.dumps(all_rows,sort_keys=True).encode()).hexdigest()
    return live, all_rows, digest


def recover(core, name, action, session=windows_session):
    core = Path(core).resolve(strict=True)
    name = bot_id(name)
    base = core/'data'
    with identity_lock(base):
        manifest, _ = read_stable(core.parent, base/'bot-runtime-owner.json', 4096)
        baseline = bot_id(_json(manifest)['baseline'])
        grant = ParentAccountGrant(core, name, baseline=name == baseline)
        digest, revision = grant._snapshot()
        path = safe_path(core.parent, base/'parent-account-admissions'/(digest+'.json'))
        check_idle(grant, path)
        if not path.exists():
            if safe_path(core.parent, path.with_suffix('.local-retired.json')).exists():
                raise ValueError('退役回执缺少对应的原准入登记')
            return '当前账号没有待恢复的准入文件。'
        raw, file_id = read_stable(core.parent, path, 1024)
        row = _json(raw)
        nonce = row.get('owner')
        expected = dict(version=1, owner=nonce, account=digest, revision=revision,
                        bot=hashlib.sha256(name.encode()).hexdigest(),
                        store=hashlib.sha256(str(grant.store).encode()).hexdigest())
        if (type(row.get('version')) is not int or not isinstance(nonce, str)
                or not re.fullmatch('[0-9a-f]{32}', nonce) or row != expected):
            raise ValueError('原准入登记与当前 Bot、账号或凭据版本不一致')
        history = safe_path(core.parent, base/'parent-account-history'/nonce)
        directory = safe_path(core.parent, base/'parent-account-crash-recovery'/nonce)
        witness = safe_path(core.parent, directory/'observed.json')
        archived = safe_path(core.parent, directory/'admission.json')
        if archived.exists():
            raise ValueError('恢复归档目标已存在')
        revision_dir = safe_path(core.parent, directory/'revisions')
        live, all_rows, revision_hash = revision_state(core.parent, path.parent, revision_dir, row)
        raw_hash = hashlib.sha256(raw).hexdigest()
        receipt, evidence = retirement_evidence(core.parent, path, nonce, raw_hash, directory, history)
        if receipt == directory/'local-retired.json' and not witness.exists():
            raise ValueError('恢复归档缺少重启前核验记录')
        store_raw, store_id = read_stable(core.parent, grant.store, 65536)
        machine, boot = session(core)
        identity = dict(version=1, machine=machine, file_id=list(file_id),
                        admission_sha256=raw_hash, receipt=evidence,
                        store_sha256=hashlib.sha256(store_raw).hexdigest(), store_id=list(store_id))
        if action == 'status':
            return ('已有重启前核验记录。' if witness.exists() else '可登记重启前核验。') + f'本次准入关联 {len(all_rows)} 份凭据修订前像。'
        if not witness.exists():
            if action != 'prepare':
                raise ValueError('请先在原机器上登记，然后正常重启 Windows')
            directory.mkdir(parents=True, exist_ok=True)
            with open_private_new(witness) as stream:
                stream.write(json.dumps(dict(identity, boot=boot, revisions_sha256=revision_hash), sort_keys=True).encode())
                stream.flush()
                os.fsync(stream.fileno())
            return '已登记。请正常重启 Windows，再运行本工具完成恢复。'
        previous = _json(read_stable(core.parent, witness, 1024)[0])
        old_boot = previous.pop('boot', None)
        previous_revisions = previous.pop('revisions_sha256', None)
        if (previous != identity or type(old_boot) is not list or len(old_boot) != 2
                or not all(type(value) is int and value > 0 for value in old_boot)):
            raise ValueError('原机器或登记文件身份有变化，已停止')
        if previous_revisions is None and all_rows:
            # Older maintenance builds observed only the primary admission. Keep that
            # witness and take a new observation of its matching before-images.
            supplement = safe_path(core.parent, directory/'revision-observation.json')
            if supplement.exists():
                extra = _json(read_stable(core.parent, supplement, 1024)[0])
                previous_revisions = extra.get('revisions_sha256')
                old_boot = extra.get('boot')
                if (type(old_boot) is not list or len(old_boot) != 2 or
                        not all(type(v) is int and v > 0 for v in old_boot)):
                    raise ValueError('修订核验记录无效')
            elif action == 'prepare':
                with open_private_new(supplement) as stream:
                    stream.write(json.dumps(dict(boot=boot,revisions_sha256=revision_hash)).encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                return '已补登记修订前像，请正常重启 Windows 后完成恢复。'
            else:
                raise ValueError('旧版核验未包含凭据修订，请先选 1 补登记，再重启 Windows')
        if previous_revisions is not None and previous_revisions != revision_hash:
            raise ValueError('原凭据修订记录发生变化，已停止')
        if action == 'prepare':
            return '重启前核验已登记，原记录已保留。'
        if action != 'finish' or boot[0] <= old_boot[0] or boot[1] <= old_boot[1]:
            raise ValueError('需要在原电脑正常重启 Windows 后完成恢复')
        check_idle(grant, path)
        if grant._snapshot() != (digest, revision) or read_stable(core.parent, path, 1024) != (raw, file_id):
            raise ValueError('账号或准入文件发生变化，已停止')
        # Windows rename refuses an existing destination. One move ends the old
        # local admission and archives its exact bytes. No new grant is issued.
        if read_stable(core.parent, grant.store, 65536) != (store_raw, store_id):
            raise ValueError('本 Bot 的账号设置发生变化，已停止')
        if retirement_evidence(core.parent, path, nonce, raw_hash, directory, history) != (receipt, evidence):
            raise ValueError('退役回执发生变化，已停止')
        if revision_state(core.parent, path.parent, revision_dir, row) != (live, all_rows, revision_hash):
            raise ValueError('凭据修订记录发生变化，已停止')
        if live:
            revision_dir.mkdir(exist_ok=True)
            for filename in sorted(live):
                (path.parent/filename).rename(revision_dir/filename)
        if receipt == path.with_suffix('.local-retired.json'):
            receipt.rename(directory/'local-retired.json')
        if history.exists():
            archived = safe_path(core.parent, history/'admission.json')
        path.rename(archived)
        return '旧准入登记已归档。可以正常启动 Bot，并重新核验 QQ 连接。'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=['status', 'prepare', 'finish'])
    parser.add_argument('bot', nargs='?')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.exit(2, '此工具仅适用于原 Windows 安装。\n')
    if bool(args.action) != bool(args.bot):
        parser.error('需要同时指定操作和 Bot 名称')
    try:
        core = Path(__file__).resolve().parent
        if args.action:
            print(recover(core, args.bot, args.action))
            return
        name = input('原安装中的 Bot 名称：').strip()
        status = recover(core, name, 'status')
        print(status)
        if status.startswith('当前账号没有'):
            return
        choice = input('1 登记重启前核验，2 重启后完成恢复，回车退出：').strip()
        if not choice:
            return
        if choice not in ('1', '2'):
            raise ValueError('未知操作')
        print(recover(core, name, 'prepare' if choice == '1' else 'finish'))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, '恢复未执行：' + str(error) + '\n')


if __name__ == '__main__':
    main()
