"""Unified launcher menu; shutdown requests never kill/adopt a process."""
from pathlib import Path
import json
import os
import sys
import time
import urllib.request
import urllib.error

from startup_terminal import header, paint, menu_header, wrap_cells, display_width, _columns
from startup_terminal import menu_notice as notice, wake_layout, wake_layout_active
from runtime_identity import safe_path, _object
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

from bot_global_admission import HostAdmissionLock
from bot_host_observer import probe_process


class MenuScreen:
    """Temporary terminal screen: redraws never append to log scrollback."""
    def __enter__(self):
        from startup_terminal import _isatty, _enable_vt
        self.active = _isatty(sys.stdout) and _enable_vt(sys.stdout)
        self.previous = None
        self.size = None
        if self.active:
            sys.stdout.write('\033[?1049h\033[?25l'); sys.stdout.flush()
        return self

    def draw(self, content):
        import shutil
        size = shutil.get_terminal_size(fallback=(100, 24))
        lines = content.splitlines()
        if not self.active:
            sys.stdout.write(content)
        elif self.previous is None or size != self.size:
            sys.stdout.write('\033[H\033[2J' + content)
        else:
            # Update only changed rows. The wordmark stays painted in place.
            for index in range(max(len(lines), len(self.previous))):
                before = self.previous[index] if index < len(self.previous) else ''
                after = lines[index] if index < len(lines) else ''
                if before != after:
                    sys.stdout.write(f'\033[{index+1};1H\033[2K' + after)
        self.previous, self.size = lines, size
        sys.stdout.flush()

    def __exit__(self, *args):
        if self.active:
            sys.stdout.write('\033[?25h\033[?1049l')
            if not wake_layout_active(): sys.stdout.write('\033[2J\033[H')
            sys.stdout.flush()


def _one_line(text, width):
    if display_width(text) <= width:
        return text
    return wrap_cells(text, width - 1)[0].rstrip() + '…'


def menu_frame(title, labels, index, *, hero=False):
    import io, shutil
    from contextlib import redirect_stdout
    original = sys.stdout
    class Buffer(io.StringIO):
        def isatty(self): return original.isatty()
        def fileno(self): return original.fileno()
    height = max(10, shutil.get_terminal_size(fallback=(100,24)).lines)
    width = _columns()
    detail = not hero and height >= 30
    large = hero and height >= 23 and width >= 74
    shortcuts = wrap_cells('[↑/↓] 选择  [Enter] 确认  [Esc] 返回', width - 4)
    capacity = max(1, (height - (17 if large else 9) - len(shortcuts) + 1) // (3 if detail else 1))
    first = (index // capacity) * capacity
    output = Buffer()
    with redirect_stdout(output):
        if height < 16 or width < 40:
            print('  ' + paint(_one_line(title, width - 4), 'accent'))
            print()
        else:
            menu_header(title, hero=large, clear=False)
        for i in range(first,min(first+capacity,len(labels))):
            label = labels[i]
            label_title, description = label if isinstance(label, tuple) else (label, '')
            # One menu row per entry, even for an unusually long Bot name.
            shown = _one_line(f'{i+1}  {label_title}', width - 5)
            print(('  › ' if i == index else '    ') + paint(shown, '96' if i == index else '90'))
            if detail:
                print('       ' + paint(_one_line(description, width - 8), 'muted'))
                print()
        if len(labels)>capacity:
            print('  ' + paint(f'{first+1}–{min(first+capacity,len(labels))} / {len(labels)}', 'muted'))
        print()
        for line in shortcuts:
            print('  ' + paint(line, 'muted'))
    # Reserve one row so the last newline never scrolls the temporary screen.
    return '\n'.join(output.getvalue().splitlines()[:height-1])


def _menu_key():
    if os.name == 'nt':
        import msvcrt
        key = msvcrt.getwch()
        if key in ('\x00', '\xe0'):
            return {'P': 'down', 'H': 'up'}.get(msvcrt.getwch(), '')
        return key
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd, termios.TCSANOW)
        key = os.read(fd, 1)
        if key == b'\x1b' and select.select([fd], [], [], .06)[0]:
            prefix = os.read(fd, 1)
            if prefix in (b'[', b'O') and select.select([fd], [], [], .06)[0]:
                return {b'A': 'up', b'B': 'down'}.get(os.read(fd, 1), '')
        return key.decode('ascii', errors='ignore') if key else '\x1b'
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, saved)


def choose(title, labels, *, hero=True):
    if wake_layout_active() and not hero:
        return _choose_inline(title, labels)
    return _choose(title, labels, hero=hero)


def _choose_inline(title, labels):
    """Compact Bot picker appended beneath self-check; never swaps screens."""
    import shutil
    from startup_terminal import _isatty, _enable_vt
    active = sys.stdin.isatty() and _isatty(sys.stdout) and _enable_vt(sys.stdout)
    for row in wrap_cells(title, _columns()-4):
        print('  ' + paint(row, 'accent'))
    print()
    labels = [' · '.join(label) if isinstance(label, tuple) else label for label in labels]
    if not active:
        for i, label in enumerate(labels):
            print(f'    {i+1}  {label}')
        while True:
            try:
                answer = input('  编号（回车返回）：').strip()
                if not answer: return None
                index = int(answer)-1
                if 0 <= index < len(labels): return index
            except ValueError: continue
            except (EOFError, KeyboardInterrupt): return None
    index = 0
    capacity = min(len(labels), max(1, min(6, shutil.get_terminal_size(fallback=(100,24)).lines//3)))
    previous = None
    sys.stdout.write('\033[?25l')
    try:
        while True:
            width = max(10, _columns()-8)
            first = index//capacity*capacity
            rows = []
            for i in range(first, first+capacity):
                text = '' if i >= len(labels) else _one_line(labels[i], width)
                rows.append(('  › ' if i == index else '    ') + paint(text,'accent' if i == index else 'muted'))
            rows += [''] + ['  ' + paint(line, 'muted') for line in wrap_cells('[↑/↓] 选择  [Enter] 确认  [Esc] 返回', max(10, _columns()-4))]
            if previous is None:
                sys.stdout.write('\n'.join(rows)+'\n')
            elif len(previous) != len(rows):
                sys.stdout.write(f'\033[{len(previous)}F\033[J' + '\n'.join(rows)+'\n')
            else:
                # Cursor remains just below this bounded list. Only repaint
                # changed rows; preceding title/check output is never touched.
                for n,row in enumerate(rows):
                    if row != previous[n]:
                        distance=len(rows)-n
                        sys.stdout.write(f'\033[{distance}F\033[2K'+row+f'\033[{distance}E')
            sys.stdout.flush();previous=rows
            key = _menu_key()
            if key in ('down', 'up'):
                index = (index + (1 if key == 'down' else -1)) % len(labels)
            elif key in ('\r', '\n'): return index
            elif key in ('\x1b', '\x03'): return None
    finally:
        sys.stdout.write('\033[?25h\n');sys.stdout.flush()


def _choose(title, labels, *, hero=True):
    index = 0
    with MenuScreen() as screen:
        while True:
            screen.draw(menu_frame(title,labels,index,hero=hero))
            if sys.stdin.isatty() and screen.active:
                key = _menu_key()
                if key in ('down', 'up'):
                    index = (index + (1 if key == 'down' else -1)) % len(labels)
                elif key in ('\r', '\n'): return index
                elif key in ('\x1b', '\x03'): return None
                elif key.isdigit() and 1 <= int(key) <= len(labels): return int(key)-1
            else:
                try:
                    answer = input('\n编号（回车返回）：').strip()
                    if not answer: return None
                    index = int(answer)-1
                    if 0 <= index < len(labels): return index
                except ValueError: pass
                except (EOFError, KeyboardInterrupt): return None


def request_stop(core, name, timeout=30):
    core=Path(core).resolve()
    marker=safe_path(core, core/'data/instances'/f'{name}.json')
    row=_object(marker)
    if set(row)!={'pid','port'} or type(row['pid']) is not int or row['pid']<=0 or type(row['port']) is not int or not 1<=row['port']<=65535:
        raise ValueError('实例登记无效，未发送休止请求')
    original=probe_process(row['pid'])
    if original.get('state')!='alive': raise ValueError('未确认有可休止的服务。请退出此部署的启动窗口，进入档案继续核对残留登记；若提示本次开机内责任未解除，请重启 Windows 后再操作。')
    origin=f"http://127.0.0.1:{row['port']}"
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    try:
        with opener.open(origin+'/infinity/bots',timeout=3) as response:
            raw=response.read(65537)
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError('无法连接登记的服务端口，尚未发送休止请求。PID 存在不代表它仍是原服务；未强制结束进程。请先在原启动窗口正常退出；窗口已不存在时，重启 Windows 后进入档案核对残留登记。') from exc
    if len(raw)>65536: raise ValueError('实例身份响应超限')
    live=json.loads(raw)
    token=live.get('shutdownInstance')
    if (live.get('active')!=name or live.get('pid')!=row['pid'] or live.get('coreRoot')!=str(core)
            or not isinstance(token,str) or len(token)!=32
            or _object(marker)!=row or probe_process(row['pid'])!=original):
        raise ValueError('实例身份变化或旧服务尚未支持此入口；请在原窗口正常退出后更新')
    request=urllib.request.Request(origin+'/shutdown',data=b'{}',method='POST',
        headers={'Origin':origin,'Content-Type':'application/json','X-Telyin-Shutdown-Instance':token})
    with opener.open(request,timeout=10) as response:
        if response.status not in (200,202): raise ValueError('休止请求未被服务接受')
    notice('shutdown','休止请求已接受，正在等待服务退出…')
    deadline=time.monotonic()+timeout
    while True:
        current=probe_process(row['pid'])
        # Process retirement, not listener disappearance, is the completion signal.
        if current.get('state')=='absent' and not marker.exists():
            notice('shutdown',f'{name} 服务已休止。');return True
        if current.get('state')=='alive' and current!=original:
            raise ValueError('原进程身份已变化；未接管新进程，请查看原启动窗口')
        if time.monotonic()>=deadline:
            notice('shutdown','休止请求已发送，但收尾尚未确认；请查看原启动窗口，未强制结束。',level='WARN')
            return False
        time.sleep(.25)


def stop_menu(core):
    core=Path(core).resolve()
    folder=safe_path(core,core/'data/instances')
    names=sorted(p.stem for p in folder.glob('*.json')) if folder.is_dir() else []
    if not names:
        menu_header('休止', hero=True)
        notice('shutdown','当前没有可休止的服务。');return True
    index=choose('休止 / 选择服务',names+['返回主菜单'])
    if index is None or index==len(names):return False
    menu_header('休止 / ' + names[index], hero=True)
    row=_object(safe_path(core, folder / (names[index]+'.json')))
    if probe_process(row.get('pid')).get('state') == 'absent':
        from bot_lifecycle import BotLifecycle
        with BotLifecycle(core).offline():
            notice('shutdown','服务进程已退出；残留端点登记已核对并归档。可返回档案继续处理。')
        return True
    lock=HostAdmissionLock(core,exclusive=False)
    try:
        lock.acquire()
        request_stop(core,names[index])
        return True
    finally: lock.close()


def main(core, start, *, profiles=None, stop=None):
    from scripts.manage_bots import main as manage
    actions = [('唤醒','选择要启动的 Bot'), ('档案','归档、迁出、恢复、改名和处理异常'),
               ('休止','正常关闭 Bot 和它托管的后台服务'), '退出']
    while True:
        option=choose('选择操作',actions,hero=True)
        if option is None or option==3:return 0
        pause = False
        try:
            if option==0:
                frame=menu_frame('选择操作',actions,0,hero=True)
                with wake_layout(frame, core=core):
                    pause = bool(start(['--start']))
            elif option==1:
                if profiles is None: manage([],core=core)
                else: pause = profiles(core)
            else: pause = (stop or stop_menu)(core)
        except (OSError,ValueError,TimeoutError) as exc:
            notice('launcher',str(exc),level='ERROR'); pause = True
        except KeyboardInterrupt:
            continue
        if pause:
            try: input('  按回车返回主菜单。')
            except (EOFError,KeyboardInterrupt):return 0
