"""Live console presentation and Windows child-process ownership."""
from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache
import json
import re
import shutil
import sys
import time
import unicodedata


_ANSI = re.compile(r'\x1b\[[0-9;]*m')
_THEME_KEYS = {
    'accent': '--color-mint', 'ink': '--color-console-ink',
    'muted': '--color-status-off', 'line': '--color-console-line',
    'warn': '--color-console-warn', 'error': '--color-console-error',
    'ok': '--color-console-ok',
}
_FALLBACK = dict(accent='#52749e', ink='#d5dee9', muted='#98a4b3',
                 line='#354252', warn='#dfbd80', error='#f29a9f', ok='#52749e')
_LEGACY = {'36': 'accent', '96': 'accent', '97': 'ink', '90': 'muted',
           '31': 'error', '91': 'error', '33': 'warn', '32': 'ok'}


@lru_cache(maxsize=1)
def _palette():
    """Read the shipped white/blue preset once, never a Bot/private setting."""
    result = dict(_FALLBACK)
    try:
        data = json.loads((Path(__file__).resolve().parent / 'presets/default.json')
                          .read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return result
        for role, key in _THEME_KEYS.items():
            value = data.get(key)
            if isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value):
                result[role] = value
    except (OSError, ValueError, UnicodeError):
        pass  # A missing appearance asset must not prevent launcher operation.
    return result


def _isatty(stream):
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _enable_vt(stream):
    if os.name != 'nt':
        return True
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetConsoleMode.restype = wintypes.BOOL
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.SetConsoleMode.restype = wintypes.BOOL
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
        mode = wintypes.DWORD()
        return bool(kernel.GetConsoleMode(handle, ctypes.byref(mode)) and
                    (mode.value & 4 or kernel.SetConsoleMode(handle, mode.value | 4)))
    except (AttributeError, OSError, ValueError):
        return False


def paint(text: str, color: str = '36', *, stream=None) -> str:
    stream = sys.stdout if stream is None else stream
    if (not _isatty(stream) or 'NO_COLOR' in os.environ
            or os.environ.get('TERM') == 'dumb' or not _enable_vt(stream)):
        return text
    role = _LEGACY.get(color, color)
    value = _palette().get(role)
    if value is None:
        return text
    rgb = ';'.join(str(int(value[i:i+2], 16)) for i in (1, 3, 5))
    weight = '1;' if color == '96' else ''
    return f'\033[{weight}38;2;{rgb}m{text}\033[0m'


def display_width(text):
    return sum(0 if unicodedata.combining(c) or unicodedata.category(c) in ('Mn', 'Me', 'Cf')
               else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1
               for c in _ANSI.sub('', text))


def _columns():
    return max(20, min(120, shutil.get_terminal_size(fallback=(100, 24)).columns))


def wrap_cells(text, width):
    """Display-cell wrapping; never truncate a name, URL, number or diagnostic."""
    width = max(2, width)
    result = []
    for paragraph in text.split('\n'):
        paragraph = paragraph.expandtabs(4) if '\t' in paragraph else paragraph
        line, used = '', 0
        # Prefer existing semantic separators; keep field values and units together.
        tokens = []
        for group in re.split(r'(?<= · )', paragraph):
            if display_width(group) <= width:
                tokens.append(group)
            else:
                tokens.extend(re.findall(r'[A-Za-z0-9_./:=\-]+|[\u3400-\u9fff]+|[^\n]', group))
        for token in tokens:
            size = display_width(token)
            if size <= width:
                if used + size > width and line:
                    result.append(line)
                    line, used = '', 0
                line += token
                used += size
                continue
            for char in token:
                size = display_width(char)
                if used + size > width and line:
                    result.append(line)
                    line, used = '', 0
                line += char
                used += size
        result.append(line)
    return result


def render_record(stamp, area, message, level, *, stream):
    # Keep the established machine-readable/redirected representation byte-for-byte.
    if not _isatty(stream) or os.environ.get('TERM') == 'dumb':
        return f'  [{stamp}] {level:<5} {area:<8} {message}\n'
    width = _columns()
    tone = 'error' if level == 'ERROR' else 'warn' if level == 'WARN' else 'muted'
    p = lambda text, role: paint(text, role, stream=stream)
    # Only the human-facing view uses severity initials; redirected logs stay intact.
    badge = {'INFO': 'I', 'WARN': 'W', 'ERROR': 'E', 'DEBUG': 'D'}.get(level, level)
    # These stable tags share a short display label with the other log sources.
    area = {'provider-replay': 'replay'}.get(area, area)
    if area == 'call' and level == 'WARN' and message.startswith('异常 · '):
        heading, separator, detail = message.partition(' · 执行失败')
        if separator and heading.count(' · ') == 1:
            message = heading + '\n执行失败' + detail
    source_width = 9
    meta = f'{stamp} {badge} {cell(area, source_width)}'
    used = display_width(meta) + 3
    if display_width(area) > source_width or used + 16 > width:
        # Keep uncommon/long source names intact on their own row, not truncated.
        lead = ''.join(p(line, tone) + '\n'
                       for line in wrap_cells(f'{stamp} {badge} {area}', width))
        prefix = '  ' + p('│', 'line') + ' '
        return lead + ''.join(prefix + p(line, 'ink') + '\n'
                              for line in wrap_cells(message, width - 4))
    prefix = (p(stamp, 'muted') + ' ' + p(badge, tone) + ' '
              + p(cell(area, source_width), 'accent') + ' ' + p('│', 'line') + ' ')
    continuation = ' ' * display_width(meta) + ' ' + p('│', 'line') + ' '
    rows = wrap_cells(message, width - used)
    return ''.join((prefix if i == 0 else continuation) + p(row, 'ink') + '\n'
                   for i, row in enumerate(rows))


def cell(text: str, width: int = 52) -> str:
    used = display_width(text)
    return text + ' ' * max(0, width - used)


def header(subtitle: str, *, clear: bool = False) -> None:
    """All launcher phases share the same layered wordmark and typography."""
    if wake_layout_active():
        phase = '01  环境自检' if ('自检' in subtitle or '环境' in subtitle or '准备' in subtitle) else '02  服务启动' if '正在启动' in subtitle else subtitle
        phase_heading(phase)
    else:
        menu_header(subtitle, hero=True, clear=clear)
    sys.stdout.flush()


def running(name: str, port: int, log: Path | None = None, *, reused: bool = False) -> None:
    # Do not clear the child's startup output when readiness is reached.
    print('\n  ' + paint('● 已连接现有实例' if reused else '● 已唤醒', 'ok'))
    for label, value, role in (('  Bot    ', name, 'ink'),
                               ('  页面   ', f'http://127.0.0.1:{port}/', 'accent')):
        indent = display_width(label)
        for index, row in enumerate(wrap_cells(value, _columns() - indent)):
            print((label if index == 0 else ' ' * indent) + paint(row, role))
    if not reused:
        note = '日志   实时输出在当前窗口，可向上滚动查看'
    else:
        note = '日志   请查看该实例原有的启动窗口'
    for row in wrap_cells(note, _columns()-4):
        print('  ' + row)
    if not reused:
        print()
        keys = 'B 打开页面   Q / Ctrl-C 休止'
        keys += '（等待任务结束，超时可能强制退出）' if os.name == 'nt' else '（等待任务结束）'
        for row in wrap_cells(keys, _columns()-4):
            print('  ' + paint(row, 'muted'))
    if wake_layout_active() and not reused:
        phase_heading('03  静候')
    print('', flush=True)


class ChildJobFailure(OSError):
    def __init__(self, job, operation, code=0):
        self.job = job  # retain native job and exact Popen even on failed construction
        super().__init__(code or 6, 'owned child job ' + operation + ' unconfirmed')


class ChildJob:
    """Owned job for legacy assignment or native birth; never a host proof.

    The existing KILL_ON_JOB_CLOSE policy is unchanged. Child processes created
    before legacy assignment and unrelated services remain untracked. The
    default birth backend passes an empty job into CreateProcessW; neither
    backend makes job close acknowledgement into all-host absence.
    """
    def __init__(self, process, *, name=None) -> None:
        self.handle = None
        self.thread_handle = None
        self.process = process
        self.assigned = self.verified = False
        if os.name != 'nt':
            return
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [('ProcessTime', ctypes.c_longlong), ('JobTime', ctypes.c_longlong),
                        ('LimitFlags', wintypes.DWORD), ('MinWorkingSet', ctypes.c_size_t),
                        ('MaxWorkingSet', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                        ('SchedulingClass', wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                'ReadOps', 'WriteOps', 'OtherOps', 'ReadBytes', 'WriteBytes', 'OtherBytes')]

        class Limits(ctypes.Structure):
            _fields_ = [('Basic', Basic), ('IO', IO), ('ProcessMemory', ctypes.c_size_t),
                        ('JobMemory', ctypes.c_size_t), ('PeakProcess', ctypes.c_size_t),
                        ('PeakJob', ctypes.c_size_t)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel.IsProcessInJob.restype = wintypes.BOOL
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel.QueryInformationJobObject.restype = wintypes.BOOL
        kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetHandleInformation.restype = wintypes.BOOL
        self.kernel = kernel
        self._limits_type = Limits
        self._process_handle = None if process is None else process._handle
        handle = kernel.CreateJobObjectW(None, name)
        creation_error = ctypes.get_last_error()
        if not handle:
            raise ChildJobFailure(self, 'creation', ctypes.get_last_error())
        if name is not None and creation_error == 183:  # ERROR_ALREADY_EXISTS
            kernel.CloseHandle(handle)
            raise ChildJobFailure(self, 'named job already exists', creation_error)
        self.handle = handle  # retain BEFORE any further failing native operation
        limits = Limits(); limits.Basic.LimitFlags = 0x2000  # existing KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ChildJobFailure(self, 'limit configuration', ctypes.get_last_error())
        if process is None:
            return  # native birth backend passes this job to CreateProcessW
        if not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(self._process_handle)):
            raise ChildJobFailure(self, 'assignment', ctypes.get_last_error())
        self.assigned = True
        self.verify()

    def verify(self):
        if os.name != 'nt':
            return False  # never emulate Windows containment elsewhere
        import ctypes
        from ctypes import wintypes
        if self.handle is None or self.process._handle is not self._process_handle:
            raise ChildJobFailure(self, 'retained handle binding')
        member = wintypes.BOOL()
        if (not self.kernel.IsProcessInJob(wintypes.HANDLE(self._process_handle), self.handle, ctypes.byref(member))
                or not member.value):
            raise ChildJobFailure(self, 'membership', ctypes.get_last_error())
        limits = self._limits_type()
        flags = wintypes.DWORD()
        if (not self.kernel.QueryInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits), None)
                or limits.Basic.LimitFlags != 0x2000
                or not self.kernel.GetHandleInformation(self.handle, ctypes.byref(flags))
                or flags.value & 1):
            raise ChildJobFailure(self, 'limits/noninheritance', ctypes.get_last_error())
        self.verified = True
        return True

    def inspect(self):
        report = dict(assigned=self.assigned, verified_before_handoff=self.verified,
            handle_retained=self.handle is not None, active_processes=None,
            complete=False, untracked_descendants=True)
        if self.handle is not None:
            import ctypes
            from ctypes import wintypes
            class Accounting(ctypes.Structure):
                _fields_ = [(name, ctypes.c_longlong) for name in ('user','kernel','period_user','period_kernel')] + [
                    (name, wintypes.DWORD) for name in ('faults','total','active','terminated')]
            accounting = Accounting()
            if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                raise ChildJobFailure(self, 'accounting query', ctypes.get_last_error())
            report['active_processes'] = accounting.active
        return report

    def close(self) -> None:
        from bot_global_admission import before_job_close, after_job_close
        before_job_close(self)
        if self.thread_handle is not None:
            if not self.kernel.CloseHandle(self.thread_handle):
                raise ChildJobFailure(self, 'primary thread close')
            self.thread_handle = None
        if self.handle is not None:
            if not self.kernel.CloseHandle(self.handle):
                import ctypes
                raise ChildJobFailure(self, 'handle close', ctypes.get_last_error())
            self.handle = None  # acknowledged native close only, never on failure
        after_job_close(self)


def console_key() -> str:
    if os.name == 'nt' and sys.stdin.isatty():
        import msvcrt
        if msvcrt.kbhit():
            key = msvcrt.getwch()
            if key in ('\x00', '\xe0'):
                msvcrt.getwch()
                return ''
            return key.lower()
    return ''


def watch(process, port: int, log: Path | None = None, *, controlled=None, shutdown=None, pump=None) -> int:
    """Watch keys/lifetime while the child writes directly to this console.

    The optional log argument remains accepted for older callers, but no file
    is opened or created. stdout/stderr belong to the inherited console handles.
    The caller must pass its own retained child, not rediscover a PID at port.
    With shutdown, Q/Ctrl-C requests cooperative cancellation on its private pipe.
    Older direct callers without it still forcibly terminate their own child.
    """
    import webbrowser
    if controlled is not None:
        from bot_startup_probe import OwnedLauncherControl
        if (type(controlled) is not OwnedLauncherControl or controlled.process is not process
                or type(port) is not int or controlled.port != port):
            raise ValueError('explicit matching controlled watch required')
    if shutdown is not None:
        from bot_launcher_child import LauncherShutdown
        if type(shutdown) is not LauncherShutdown or shutdown.process is not process:
            raise ValueError('matching private launcher shutdown required')
    if pump is not None and (controlled is None or not callable(pump)):
        raise ValueError('controlled launcher pump required')
    while process.poll() is None:
        if pump is not None:pump()
        try:
            if controlled is not None:
                if not controlled.check(allow_exited=True):
                    return process.returncode
            key = console_key()
            if key == 'b':
                active_port = port if controlled is None else controlled.ready_port()
                webbrowser.open(f'http://127.0.0.1:{active_port}/#/persona')
            elif key in ('q', '\x03'):
                raise KeyboardInterrupt
            time.sleep(0.25)
        except KeyboardInterrupt:
            if shutdown is not None:
                from launcher_presentation import notice
                notice('shutdown', '已请求休止，正在等待 Bot 完成任务并关闭后台服务…')
                if controlled is not None:
                    if not controlled.check(allow_exited=True):
                        return process.returncode
                exited = process.poll()
                if exited is not None:
                    return exited
                shutdown.request()
                # Timeout/error propagates. main finally may force OWN Job/child
                # cleanup; never rediscover or send HTTP to an old service port.
                return process.wait(timeout=8)
            if controlled is not None:
                from launcher_presentation import notice
                notice('shutdown', '正在结束本次控制器…')
                controlled.stop()
                return process.wait(timeout=8)
            # Default callers retain the Popen created by this invocation. A
            # port (including a readiness port) is NOT shutdown authority.
            # This is forced termination, not application flush/offline proof.
            from launcher_presentation import notice
            notice('shutdown', '正在强制结束本次控制器，未确认数据已保存。', level='WARN')
            exited = process.poll()
            if exited is not None:
                return exited
            process.terminate()
            # Propagate timeout/error/cancellation; do not fabricate success.
            # The caller still owns its original Job/handle cleanup in finally.
            return process.wait(timeout=8)
    return process.returncode


_WORDMARK = {
    'T': ('█████','  █  ','  █  ','  █  ','  █  '),
    'E': ('█████','█    ','████ ','█    ','█████'),
    'L': ('█    ','█    ','█    ','█    ','█████'),
    'Y': ('█   █',' █ █ ','  █  ','  █  ','  █  '),
    'I': ('█████','  █  ','  █  ','  █  ','█████'),
    'N': ('█   █','██  █','█ █ █','█  ██','█   █'),
    'C': (' ████','█    ','█    ','█    ',' ████'),
    'O': (' ███ ','█   █','█   █','█   █',' ███ '),
    'R': ('████ ','█   █','████ ','█  █ ','█   █'),
    ' ': ('   ','   ','   ','   ','   '),
}


def layered_wordmark():
    """Solid face with two offset, hollow contours in terminal cells."""
    rows = [' '.join(_WORDMARK[c][r] for c in 'TELYIN CORE') for r in range(5)]
    face = {(x,y) for y,row in enumerate(rows) for x,c in enumerate(row) if c == '█'}
    outline = {
        'T': ('┌───┐','└┐ ┌┘',' │ │ ',' │ │ ',' └─┘ '),
        'E': ('┌───┐','│ ┌─┘','│ ├─┐','│ └─┤','└───┘'),
        'L': ('┌─┐  ','│ │  ','│ │  ','│ └─┐','└───┘'),
        'Y': ('┌┐ ┌┐','└┐┌┘│',' └┤ ┘','  │ │','  └─┘'),
        'I': ('┌───┐','└┐ ┌┘',' │ │ ','┌┘ └┐','└───┘'),
        'N': ('┌┐ ┌┐','│└┐││','│ └┤│','││┐ │','└┘└─┘'),
        'C': ('┌───┐','│ ┌─┘','│ │  ','│ └─┐','└───┘'),
        'O': ('┌───┐','│┌─┐│','││ ││','│└─┘│','└───┘'),
        'R': ('┌───┐','│┌─┐│','│└─┬┘','│┌┐└┐','└┘└─┘'),
        ' ': ('   ',)*5,
    }
    contour = [' '.join(outline[c][r] for c in 'TELYIN CORE') for r in range(5)]
    canvas = {}
    for offset, role in ((2, 'line'), (1, 'muted')):
        for y,row in enumerate(contour):
            for x,glyph in enumerate(row):
                if glyph != ' ': canvas[x+offset,y+offset]=(glyph,role)
    for pos in face: canvas[pos]=('█','accent')
    return [[canvas.get((x,y),(' ','accent')) for x in range(len(rows[0])+2)] for y in range(7)]


def menu_header(subtitle, *, hero=False, clear=True):
    """Responsive text-only wordmark; terminal colors/background remain user's."""
    if clear and _isatty(sys.stdout) and _enable_vt(sys.stdout):
        print('\033[2J\033[H', end='')
    width = _columns()
    print()
    if hero and width >= 74 and _isatty(sys.stdout):
        for row in layered_wordmark():
            print('  ' + ''.join(paint(char, role) for char, role in row).rstrip())
    else:
        print('  ' + paint('TELYIN CORE', 'accent'))
    if hero:
        version = ''
        try:
            version = json.loads((Path(__file__).resolve().parent.parent/'candidate.json').read_text()).get('version','')
        except (OSError,ValueError): pass
        print()
        for line in wrap_cells('本地控制台' + ('  /  ' + version if version else ''), width - 4):
            print('  ' + paint(line, 'muted'))
    print()
    for line in wrap_cells('— ' + subtitle + ' —', width - 4):
        print('  ' + paint(line, 'accent'))
    print()


def wake_layout_active():
    return os.environ.get('CORE_LAUNCHER_WAKE_LAYOUT') == '1'


def phase_heading(title):
    print()
    for row in wrap_cells(title, _columns()-4):
        print('  ' + paint(row, 'accent'))
    print('  ' + paint('─' * min(64, _columns()-4), 'line'))
    print('', flush=True)


def menu_notice(area, text, *, level='INFO'):
    """Action pages use menu typography, not service log timestamps."""
    role = 'error' if level == 'ERROR' else 'warn' if level == 'WARN' else 'ink'
    label = '未完成' if level == 'ERROR' else '提示' if level == 'WARN' else '详情' if area == 'report' else ''
    for line in wrap_cells((label + ' · ' if label else '') + text, _columns()-6):
        print('    ' + paint(line, role))
    print('', flush=True)


from contextlib import contextmanager

@contextmanager
def wake_layout(frame, *, core=None):
    """Keep the selected main page, then append check/start/live sections once."""
    key='CORE_LAUNCHER_WAKE_LAYOUT'
    previous=os.environ.get(key)
    os.environ[key]='1'
    try:
        print(frame, flush=True)
        yield
    finally:
        if previous is None: os.environ.pop(key,None)
        else: os.environ[key]=previous
