"""Advisory local storage accounting. Never modify history or gate a write.

Totals are logical file bytes observed during a bounded, non-atomic scan, not
physical allocation or a hard quota. Incomplete scans retain lower bounds and
never report that everything is within budget. No model or network integration.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from .storage_trend import CATEGORIES, category
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time

GIB = 1024 ** 3
SCHEMA = 'telyincore-storage-watch-v1'
MAX_STATE = 1024 * 1024


@dataclass(frozen=True)
class Policy:
    bot_bytes: int = 10 * GIB
    total_bytes: int = 50 * GIB
    free_bytes: int = 10 * GIB
    notify_interval: int = 3600

    def __post_init__(self):
        for value in (self.bot_bytes, self.total_bytes, self.free_bytes):
            if type(value) is not int or not 0 < value <= 1024 ** 5:
                raise ValueError('positive bounded byte thresholds required')
        if type(self.notify_interval) is not int or not 60 <= self.notify_interval <= 86400:
            raise ValueError('notification interval must be 60..86400 seconds')


def plain(path):
    info = Path(path).lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('linked storage path')
    return info


def control(path):
    """Read only a bounded identity/policy sidecar, never conversation data."""
    before = plain(path)
    if not stat.S_ISREG(before.st_mode) or before.st_size > 65536:
        raise ValueError('invalid storage control file')
    with Path(path).open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('storage control changed')
        raw = stream.read(65537)
    after = plain(path)
    if len(raw) > 65536 or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('storage control changed')
    try:
        value = json.loads(raw)
    except RecursionError as error:
        raise ValueError('storage control nesting limit exceeded') from error
    if not isinstance(value, dict):raise ValueError('storage control must be an object')
    return value


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 64 or any(c in value for c in '/\\\r\n\0'):
        raise ValueError('invalid storage owner label')
    return value


def _history(path):
    return ('.jsonl' in path.name.casefold() or 'record-history' in path.parts
            or path.name == 'synthetic-history.bin')


class ScanLimit(Exception):
    pass


def scan(root, policy=Policy(), *, max_entries=200000, seconds=20, clock=time.monotonic):
    """Observe known local layouts; never create an identity or follow a link."""
    root = Path(root).absolute()
    root_info = plain(root)
    if not stat.S_ISDIR(root_info.st_mode):raise ValueError('storage root must be a directory')
    if type(max_entries) is not int or not 1 <= max_entries <= 1000000:
        raise ValueError('entry bound required')
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 30:
        raise ValueError('finite scan deadline required')
    deadline = clock() + seconds
    categories = dict.fromkeys(CATEGORIES, 0)
    groups = {}; total = other = visited = 0; errors = set(); stopped = False
    def check():
        nonlocal visited, stopped
        visited += 1
        if visited > max_entries or clock() >= deadline:
            stopped = True;errors.add('scan_limit');raise ScanLimit
    def label(area, name):
        return area + '/' + _name(name).casefold()
    def owner(path, key, fallback):
        try:
            if not os.path.lexists(path):return fallback
            return _name(control(path).get(key))
        except (OSError, ValueError, TypeError):
            errors.add('owner_metadata_unavailable');return fallback
    def walk(path, group, depth=0):
        nonlocal total, other
        check()
        try:
            info = plain(path)
            if stat.S_ISDIR(info.st_mode):
                if depth >= 32:errors.add('depth_limit');return
                with os.scandir(path) as entries:
                    for entry in entries:walk(Path(entry.path), group, depth + 1)
                after = plain(path)
                if (info.st_dev, info.st_ino) != (after.st_dev, after.st_ino):errors.add('directory_changed')
            elif stat.S_ISREG(info.st_mode):
                categories[category(path)] += info.st_size
                if _history(path):
                    total += info.st_size
                    if group is None:
                        errors.add('history_owner_unknown');key='unassigned'
                    else:key=group
                    if key not in groups and len(groups) >= 1024:
                        errors.add('owner_limit');key='unassigned'
                    groups[key] = groups.get(key, 0) + info.st_size
                else:other += info.st_size
            else:errors.add('special_file')
        except FileNotFoundError:errors.add('source_changed')
        except (OSError, ValueError):errors.add('source_unavailable_or_linked')
    def entries(path):
        check()
        try:
            if not os.path.lexists(path):return
            if not stat.S_ISDIR(plain(path).st_mode):errors.add('invalid_directory');return
            with os.scandir(path) as iterator:
                for entry in iterator:yield Path(entry.path)
        except (OSError, ValueError):errors.add('source_unavailable_or_linked')
    def archives(path, area):
        for archive in entries(path):
            try:
                if not stat.S_ISDIR(plain(archive).st_mode):continue
                name=owner(archive/'transaction.json', 'name', None)
                walk(archive, label(area,name) if name is not None else None)
            except (OSError, ValueError):errors.add('source_unavailable_or_linked')
    def layout(base, area):
        # Check every intermediate directory before opening any descendants.
        for intermediate in [base, base/'core']:
            if not os.path.lexists(intermediate):return
            if not stat.S_ISDIR(plain(intermediate).st_mode):raise ValueError('invalid layout')
        data=base/'core/data'
        if os.path.lexists(data):plain(data)
        baseline=owner(data/'bot-runtime-owner.json','baseline',None)
        if baseline is None:baseline=owner(data/'active-bot.json','baseline',None)
        for child in entries(data):
            if child.name=='bot-runtimes':
                for runtime in entries(child):walk(runtime, label(area,runtime.name))
            else:walk(child, label(area,baseline) if baseline is not None else None)
        for bot in entries(base/'bots'):
            if bot.name=='.archives':archives(bot,area)
            elif bot.name.startswith('.'):
                walk(bot,None)  # preserved, unclassified history still counts
            else:walk(bot,label(area,bot.name))
        archives(base/'bot-archives',area)
    try:
        layout(root,'installed')
        for name in ['.linux-acceptance', '.linux-dev']:
            workspace=root/'core'/name
            if os.path.lexists(workspace):layout(workspace,name)
    except ScanLimit:pass
    except (OSError, ValueError):errors.add('layout_unavailable_or_linked')
    try:free = shutil.disk_usage(root).free
    except OSError:free=None;errors.add('volume_unavailable')
    temporary_path = None; temporary_free = None; temporary_same_volume = None; temporary_device = None
    try:
        # Derived snapshots use the observer user's current tempfile location,
        # which may live on another volume (e.g. install F:, cache C:).
        temporary = Path(tempfile.gettempdir())
        temporary_path = str(temporary)
        temporary_device = temporary.stat().st_dev
        temporary_same_volume = temporary_device == root_info.st_dev
        temporary_free = free if temporary_same_volume else shutil.disk_usage(temporary).free
    except OSError:
        errors.add('temporary_volume_unavailable')
    from .temp_volumes import volumes
    known={root_info.st_dev:free}
    if temporary_device is not None and temporary_free is not None:
        known[temporary_device]=temporary_free
    registered,registered_alerts,registered_errors=volumes(root,known,policy)
    errors.update(registered_errors)
    alerts=list(registered_alerts)
    for name, size in sorted(groups.items()):
        if name!='unassigned' and size >= policy.bot_bytes:
            alerts.append({'key':'bot:'+name,'kind':'bot_history','bytes':size,'threshold':policy.bot_bytes})
    if total >= policy.total_bytes:alerts.append({'key':'history','kind':'all_history','bytes':total,'threshold':policy.total_bytes})
    if free is not None and free < policy.free_bytes:alerts.append({'key':'free','kind':'low_free_space','bytes':free,'threshold':policy.free_bytes})
    if temporary_same_volume is False and temporary_free is not None and temporary_free < policy.free_bytes:
        alerts.append({'key':'temporary_free','kind':'low_temporary_space','bytes':temporary_free,'threshold':policy.free_bytes})
    if errors:alerts.append({'key':'incomplete','kind':'incomplete_inventory','reasons':sorted(errors)})
    return dict(schema=SCHEMA,at=time.time(),complete=not errors,history_bytes=total,
                other_observed_bytes=other,by_owner=groups,category_bytes=categories,
                category_accounting="disjoint path-based categories within observed Bot/runtime roots; not semantic originals, not whole installation; legacy history total unchanged",free_bytes=free,entries=min(visited,max_entries),
                temporary_path=temporary_path,temporary_free_bytes=temporary_free,
                temporary_same_volume=temporary_same_volume,registered_temporary_roots=registered,
                temporary_coverage='installation, observer default and successfully registered Core roots; not all users/processes',
                errors=sorted(errors),alerts=alerts,policy=vars(policy),
                accounting='logical bytes of JSONL/retained copies, record-history and synthetic-history; non-atomic metadata scan; no hard quota',
                originals_modified=False,writes_blocked=False)


def state_directory(root):
    root=Path(root).absolute();plain(root)
    folder=root/'.storage-watch'
    if not os.path.lexists(folder):folder.mkdir(mode=0o700)
    if not stat.S_ISDIR(plain(folder).st_mode):raise ValueError('invalid monitor state directory')
    return folder


@contextmanager
def exclusive(folder, *, name='monitor.lock'):
    path=folder/name
    if os.path.lexists(path):plain(path)
    with path.open('a+b') as stream:
        if os.name=='nt':
            import msvcrt
            stream.seek(0,2)
            if stream.tell()==0:stream.write(b'0');stream.flush()
            stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            if os.name=='nt':stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def atomic(folder, name, raw):
    if len(raw)>MAX_STATE:raise ValueError('monitor state size limit')
    path=folder/name
    if os.path.lexists(path):plain(path)
    fd,temp=tempfile.mkstemp(prefix='.storage-',dir=folder)
    try:
        with os.fdopen(fd,'wb') as output:output.write(raw);output.flush();os.fsync(output.fileno())
        os.replace(temp,path)
    finally:Path(temp).unlink(missing_ok=True)


def describe(report):
    gib=lambda n:f'{n/GIB:.2f} GiB'
    from datetime import datetime,timezone
    lines=['统计时间：'+datetime.fromtimestamp(report['at'],timezone.utc).isoformat(),
           'TelyinCore 本地容量监测：只告警，不删除原文、不阻断写入。',
           '统计完整。' if report['complete'] else '统计不完整：已计入的大小只是下界，不能认定容量正常。',
           '已观察历史：'+gib(report['history_bytes']),
           '安装卷可用空间：'+(gib(report['free_bytes']) if report['free_bytes'] is not None else '未知')]
    if 'temporary_free_bytes' in report:
        value=report['temporary_free_bytes']
        lines.append('临时缓存卷可用空间：'+(gib(value) if value is not None else '未知')
                     + ('（与安装同卷）' if report['temporary_same_volume'] else ''))
    if 'registered_temporary_roots' in report:
        lines.append('已登记Core临时目录：'+str(len(report['registered_temporary_roots']))+'；仅覆盖成功登记路径，不代表其他进程/用户的全部临时目录。')
    if 'category_bytes' in report:
        labels=['原文/可编辑文本','向量','已识别派生索引','媒体','压缩包（用途未判定）','未分类数据库','其他']
        lines.append('同一扫描范围分项（路径分类，保留副本分别计入）：')
        lines.extend(label+'：'+gib(report['category_bytes'][key]) for key,label in zip(CATEGORIES,labels))
        trend = report.get('trend', {})
        delta = trend.get('delta_bytes')
        if delta is not None:
            changes = [f'{label} {delta[key]:+,} 字节' for key, label in zip(CATEGORIES, labels) if delta[key]]
            lines.append(f"距上次完整采样 {trend['delta_interval_seconds']:g} 秒的分项净变化：" +
                         ('、'.join(changes) if changes else '0 字节'))
    if report.get('trend',{}).get('reason'):
        lines.append('趋势未写入：'+report['trend']['reason'])
    for alert in report['alerts'][:12]:
        if alert['kind']=='bot_history':lines.append(alert['key'][4:]+': 历史 '+gib(alert['bytes'])+'，达到软阈值 '+gib(alert['threshold']))
        elif alert['kind']=='all_history':lines.append('全 Core 历史达到软阈值 '+gib(alert['threshold']))
        elif alert['kind']=='low_free_space':lines.append('磁盘余量低于 '+gib(alert['threshold'])+'，请处理空间。')
        elif alert['kind']=='low_registered_temporary_space':lines.append('Core登记的临时卷余量低于 '+gib(alert['threshold'])+'，请处理空间。')
        elif alert['kind']=='low_temporary_space':lines.append('临时缓存所在卷余量低于 '+gib(alert['threshold'])+'，请处理空间。')
        else:lines.append('未完成项：'+', '.join(alert['reasons']))
    if len(report['alerts'])>12:lines.append('其余告警见 status.json。')
    lines.append('统计为逻辑文件字节及保留副本；不是物理占用或硬配额保证。')
    return '\n'.join(lines)+'\n'


def notify(text):
    """Request a local desktop notification; success is not delivery proof."""
    try:
        if os.name=='nt':
            import base64
            message=base64.b64encode(text[:1500].encode()).decode()
            script="Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; $n=New-Object System.Windows.Forms.NotifyIcon; try {$n.Icon=[System.Drawing.SystemIcons]::Warning; $n.Visible=$true; $n.BalloonTipTitle='TelyinCore'; $n.BalloonTipText=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+message+"')); $n.ShowBalloonTip(10000); Start-Sleep -Seconds 12} finally {$n.Dispose()}"
            command=['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()]
        else:
            binary=shutil.which('notify-send')
            if not binary:return False
            command=[binary,'--app-name=TelyinCore','--urgency=critical','--expire-time=10000','TelyinCore 容量告警',text[:1500]]
        result=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=18)
        return result.returncode==0
    except (OSError,subprocess.SubprocessError):return False


def run_once(root, *, policy=None, notifier=notify, now=time.time, scanner=scan):
    folder=state_directory(root)
    with exclusive(folder):
        if policy is None:
            config=folder/'policy.json'
            policy=Policy(**control(config)) if os.path.lexists(config) else Policy()
        report=scanner(root,policy);stamp=now()
        last=None
        try:
            state=folder/'notifications.json'
            if os.path.lexists(state):last=control(state).get('last_requested_at')
        except (OSError,ValueError,TypeError):pass
        from .storage_trend import valid_timestamp
        if not valid_timestamp(last):last=None
        recent=(type(last) in (int,float) and math.isfinite(last)
                and 0 <= stamp-last < policy.notify_interval)
        due=bool(report['alerts']) and not recent
        # Always publish fresh status, even if a desktop is unavailable or the
        # same alert is throttled. Nothing is appended to an unbounded log.
        from .storage_trend import update
        if 'category_bytes' in report:
            try:report['trend']=update(folder,report)
            except (OSError,ValueError):report['trend']={'recorded':False,'reason':'trend_unavailable','delta_bytes':None}
        text=describe(report)
        atomic(folder,'status.json',json.dumps(report,ensure_ascii=False,indent=2).encode())
        atomic(folder,'容量状态.txt',text.encode())
        requested=due and bool(notifier(text))
        state={'schema':SCHEMA,'last_requested_at':stamp if requested else last,
               'notification_requested':requested,'delivery_confirmed':False,
               'alert_count':len(report['alerts']),'at':stamp}
        atomic(folder,'notifications.json',json.dumps(state,ensure_ascii=False).encode())
        return report,state
