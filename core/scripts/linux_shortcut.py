"""Register this extracted deployment in the user's application menu."""
import os
from pathlib import Path
import shutil
import tempfile


def exec_arg(path):
    # Desktop Entry unescapes the string, then tokenizes the quoted Exec value.
    value = str(path).replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return '"' + value.replace('\\', '\\\\') + '"'


def write_entry(folder, filename, entry):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, delete=False) as out:
        out.write(entry)
    os.replace(out.name, folder/filename)


def install():
    script = Path(__file__).resolve().parents[2] / '启动 Telyincore.sh'
    if any(ch in str(script) for ch in '\r\n\t'):
        raise ValueError('部署路径包含控制字符')
    base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    folder = base / 'applications'
    folder.mkdir(parents=True, exist_ok=True)
    kitty = shutil.which('kitty')
    command = [kitty, '--title', 'TelyinCore'] if kitty else []
    command.extend(['bash', script])
    entry = '\n'.join(['[Desktop Entry]', 'Type=Application', 'Name=TelyinCore 启动',
                       'Exec=' + ' '.join(map(exec_arg, command)),
                       'Terminal=' + ('false' if kitty else 'true'), 'Icon=utilities-terminal',
                       'Categories=Utility;', 'Keywords=TelyinCore;Core;Bot;启动;', ''])
    write_entry(folder, 'telyincore-linux.desktop', entry)
    print('已添加应用菜单入口「TelyinCore 启动」。')
    if (script.parent / 'core/linux-components').is_dir() and os.uname().machine == 'x86_64':
        qq_entry = '\n'.join(['[Desktop Entry]', 'Type=Application', 'Name=QQ（TelyinCore）',
                              'Exec=' + ' '.join(map(exec_arg, ['bash', script, '--start-qq'])),
                              'Terminal=false', 'Icon=qq', 'Categories=Network;InstantMessaging;',
                              'Keywords=QQ;TelyinCore;SnowLuma;', ''])
        write_entry(folder, 'telyincore-qq.desktop', qq_entry)
        print('已添加应用菜单入口「QQ（TelyinCore）」。')


if __name__ == '__main__':
    install()
