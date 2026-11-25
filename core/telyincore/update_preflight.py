"""Advisory live check. The installer still takes the exclusive offline locks."""
import os
import subprocess
from pathlib import Path

from .online_install import safe_file


class UpdateBlocked(ValueError):
    pass


def check_before_exit(root, platform, port):
    core = Path(root).resolve() / 'core'
    try:
        if platform == 'win':
            _windows(core, port)
        elif platform == 'linux':
            _linux(core)
        else:
            raise ValueError('无法识别部署平台')
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise UpdateBlocked(f'更新已暂停，Core 继续运行。{exc}') from exc


def _windows(core, port):
    from bot_global_admission import HostAdmissionLock, source_pins, validate_admission_metadata
    from bot_host_observer import probe_process
    from bot_lifecycle_boot import boot_filetime, belongs_to_current_boot
    from runtime_identity import identity_lock, _object
    from scripts.maintenance_prepare import running_services

    base = safe_file(core, 'data')
    if not base.is_dir():
        raise ValueError('无法读取此部署的运行登记目录。')
    lock = HostAdmissionLock(core, exclusive=False)
    try:
        lock.acquire()
    except (ValueError, OSError) as exc:
        raise ValueError(f'暂时无法检查启动登记，请结束档案维护后重试。如仍失败，请查看启动器诊断（{exc}）。') from exc
    try:
        with identity_lock(base):
            if safe_file(base, 'host-maintenance.json').exists():
                raise ValueError('此部署有未完成的档案维护。请完成或恢复原维护操作，再点更新。')
            validate_admission_metadata(base)
            others = running_services(core, except_instance=(os.getpid(), port))
            if others:
                raise ValueError('请先从启动器正常休止其他 Bot（' + '、'.join(others) + '），再点更新。')
            folder = safe_file(base, 'host-start-owners')
            rows = [_object(safe_file(folder, p.name)) for p in sorted(folder.iterdir())] if folder.exists() else []
            if not rows:
                return
            boot = boot_filetime()
            parent = probe_process(os.getppid())
            own_seen = False
            unresolved = []
            active = []
            unknown = []
            for row in rows:
                owner = row['owner']
                if not belongs_to_current_boot(owner, boot):
                    continue
                if owner == parent and not own_seen:
                    own_seen = True
                    if row['entry_sources'] != source_pins(core):
                        raise ValueError('当前启动器运行期间，启动程序文件发生变化。请保留启动日志并联系维护者核对版本。')
                    continue
                observed = probe_process(owner['pid'])
                if observed == owner:
                    active.append(str(owner['pid']))
                elif observed.get('state') in ('alive', 'absent'):
                    unresolved.append(str(owner['pid']))
                else:
                    unknown.append(str(owner['pid']))
            reasons = []
            if active:
                reasons.append('其他启动器仍在运行（PID ' + '、'.join(active) + '）。请从对应启动窗口正常休止并退出后重试。')
            if unresolved:
                reasons.append('旧启动器已退出，但缺少完整的进程组退出凭据（PID ' + '、'.join(unresolved)
                               + '）。可保留启动器诊断交给维护者核对；若无法补齐凭据，保存工作并重启 Windows 后再安装。')
            if unknown:
                reasons.append('暂时无法核实启动器状态（PID ' + '、'.join(unknown) + '）。请查看启动器诊断后重试。')
            if reasons:
                raise ValueError(' '.join(reasons))
    finally:
        lock.close()


def _linux(core):
    import fcntl
    for name, activity in (('.linux-preparation.lock', '环境准备'), ('.linux-acceptance-launch.lock', '另一项服务或启动操作')):
        path = core / name
        if not path.exists():
            continue
        if name == '.linux-acceptance-launch.lock':
            # start.sh execs the active service with its exclusive lock on fd 9.
            own_fd = Path('/proc/self/fd/9')
            if own_fd.exists() and own_fd.samefile(path) and 'FLOCK  ADVISORY  WRITE' in Path('/proc/self/fdinfo/9').read_text():
                continue
        with path.open('rb') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError(f'此部署正在进行{activity}，请等待它完成后重试。') from exc
