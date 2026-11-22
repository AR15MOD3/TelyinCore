"""Start the user's installed QQ through its ordinary desktop entry."""
import configparser
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def qq_identity(pid):
    proc = Path('/proc') / str(pid)
    try:
        if proc.stat().st_uid == os.getuid():
            birth = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
            image = (proc / 'exe').resolve(strict=True)
            args = (proc / 'cmdline').read_bytes().split(b'\0')
            if (image.name.lower() == 'qq' and not any(a.startswith(b'--type=') for a in args)
                    and (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19] == birth):
                boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                return dict(pid=pid, state='alive', image=str(image),
                            created_filetime=int.from_bytes(hashlib.sha256(f'{boot}:{birth}'.encode()).digest()[:8],'big'))
    except (OSError, IndexError, ValueError):
        pass
    return dict(pid=pid, state='unknown')


def qq_processes():
    rows = []
    for comm in Path('/proc').glob('[0-9]*/comm'):
        try:
            if comm.read_text().strip().lower() != 'qq':
                continue
            row = qq_identity(int(comm.parent.name))
            if row['state'] == 'alive':
                rows.append({'ProcessId': row['pid'], 'ExecutablePath': row['image']})
        except OSError:
            continue
    return rows


def is_qq(path):
    name = Path(path).name.lower()
    return name in ('qq', 'linuxqq') or (name.startswith('qq') and name.endswith('.appimage'))


def valid_qq(path):
    return is_qq(path) and Path(path).is_file() and os.access(path, os.X_OK)


def desktop_command(selected=''):
    folders = [os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share',
               *os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':')]
    for folder in filter(None, folders):
        apps = Path(folder)/'applications'
        common = [apps/name for name in ('qq.desktop', 'linuxqq.desktop', 'com.qq.QQ.desktop')]
        candidates = sorted(p for p in apps.glob('*.desktop') if 'qq' in p.name.lower())
        for entry in dict.fromkeys(common + candidates):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(entry, encoding='utf-8')
                args = shlex.split(parser['Desktop Entry']['Exec'])
            except (KeyError, ValueError, configparser.Error):
                continue
            if not args:
                continue
            binary = shutil.which(args[0])
            if binary and valid_qq(binary) and (not selected or Path(binary).samefile(selected)):
                return [binary, *[a for a in args[1:] if a not in ('%u','%U','%f','%F','%i','%c','%k')]]
    binary = selected or shutil.which('qq') or '/opt/QQ/qq'
    if valid_qq(binary):
        return [str(binary)]
    raise ValueError('未找到桌面 QQ，请指定已安装的 qq 或 QQ AppImage 的完整路径。')


def start(selected=''):
    command = desktop_command(selected)
    subprocess.Popen(command, start_new_session=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(start(sys.argv[1] if len(sys.argv) > 1 else ''))
