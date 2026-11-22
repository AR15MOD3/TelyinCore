"""Read-only status query and fixed reply to the authenticated inbound conversation."""
import re
import sqlite3
import time
from contextlib import closing


def status_text(post):
    version = (post('get_version_info', {}).get('data') or {}).get('app_version')
    state = post('get_status', {}).get('data') or {}
    lines = ['SnowLuma 状态']
    if isinstance(version, str) and re.fullmatch(r'[A-Za-z0-9.+_-]{1,60}', version):
        lines.append('版本 ' + version)
    online, good = state.get('online'), state.get('good')
    lines.append('QQ ' + {True:'在线', False:'离线'}.get(online, '状态未知'))
    lines.append('协议 ' + {True:'正常', False:'异常'}.get(good, '状态未知'))
    seconds = state.get('time')
    if type(seconds) is int and seconds >= 0:
        hours, minutes = divmod(seconds // 60, 60)
        lines.append(f'运行时长 {hours} 小时 {minutes} 分钟')
    return '\n'.join(lines)


def claim(directory, key):
    # Keep an uncertain send reserved too. A repeated WS delivery cannot send it twice.
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory/'status-commands.sqlite3', timeout=2)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS commands (key TEXT PRIMARY KEY, seen INTEGER NOT NULL)')
        now = int(time.time())
        db.execute('DELETE FROM commands WHERE seen < ?', (now-7*86400,))
        return db.execute('INSERT OR IGNORE INTO commands VALUES (?,?)', (key, now)).rowcount == 1
