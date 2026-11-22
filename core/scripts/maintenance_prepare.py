"""Graceful preparation for an explicitly confirmed interactive offline rename.

Never terminate/adopt processes, erase ownership records, or reuse a maintenance
lock as permission to mutate. The transaction must still acquire its own lock.
"""
import time
from pathlib import Path
from bot_global_admission import HostAdmissionLock, validate_admission_metadata
from bot_host_observer import probe_process
from bot_registry import bot_id
from runtime_identity import safe_path, _object

BUSY = ('仍有启动窗口正在唤醒或收尾，档案尚未改名。'
        '请在原窗口完成休止并退出运行流程后重试；网页暂停不等于服务退出。')


def running_services(core):
    core = Path(core).resolve()
    folder = safe_path(core, core / 'data/instances')
    if not folder.exists():
        return []
    names = []
    for path in sorted(folder.iterdir()):
        path = safe_path(core, path)
        if not path.is_file() or path.suffix != '.json':
            raise ValueError('实例登记目录异常，未发送休止请求')
        bot_id(path.stem)
        row = _object(path)
        if (set(row) != {'pid', 'port'} or type(row['pid']) is not int or row['pid'] <= 0
                or type(row['port']) is not int or not 1 <= row['port'] <= 65535):
            raise ValueError('实例登记无效，未发送休止请求')
        state = probe_process(row['pid']).get('state')
        if state == 'alive':
            names.append(path.stem)
        elif state != 'absent':
            raise ValueError('无法确认服务进程状态，未发送休止请求')
    return names


def check_owner_history(core, lock):
    core = Path(core).resolve()
    base = core / 'data'
    validate_admission_metadata(base, held_lock=lock)
    folder = safe_path(core, base / 'host-start-owners')
    if folder.exists():
        rows = [_object(safe_path(core, path)) for path in sorted(folder.iterdir())]
        if rows:
            from bot_lifecycle_boot import previous_boot_owners
            previous_boot_owners(rows)


def preflight_rename(core):
    """Report retained owner blockers before collecting a new name/confirmation."""
    if not (Path(core) / 'data').exists():
        return
    lock = HostAdmissionLock(core, exclusive=True)
    try:
        lock.acquire()
    except (OSError, ValueError) as exc:
        if isinstance(exc, BlockingIOError) or str(exc) == 'another startup/maintenance admission owns this core':
            return  # Live owners are addressed by explicit graceful-stop confirmation.
        raise
    try:
        check_owner_history(core, lock)
    finally:
        lock.close()


def wait_for_maintenance(core, names, timeout=5):
    # Only stop services disclosed before confirmation. A newly started service
    # still holds admission and causes refusal, rather than being stopped unseen.
    from scripts.core_menu import request_stop
    for name in names:
        marker = safe_path(core, Path(core) / 'data/instances' / (name + '.json'))
        if not marker.exists():
            continue
        if not request_stop(core, name):
            raise ValueError('服务收尾尚未完成，档案没有改名；请查看原启动窗口。')
    deadline = time.monotonic() + timeout
    while True:
        lock = HostAdmissionLock(core, exclusive=True)
        try:
            lock.acquire()
        except (OSError, ValueError) as exc:
            busy = isinstance(exc, BlockingIOError) or str(exc) == 'another startup/maintenance admission owns this core'
            if not busy:
                raise
            if time.monotonic() >= deadline:
                raise ValueError(BUSY) from exc
            time.sleep(.1)
        else:
            try:
                check_owner_history(core, lock)
            finally:
                lock.close()
            return
