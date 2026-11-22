"""Stage an official platform delta and apply data files at launcher startup."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen

MAX_BYTES = 256 * 1024 * 1024
SUM = re.compile(r'[a-f0-9]{64}')
PLATFORMS = {'win', 'linux'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, raw, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
    try:
        with temp.open('wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        temp.chmod(mode)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def safe_file(root, name):
    parts = PurePosixPath(name).parts
    if (not parts or '/'.join(parts) != name or '..' in parts or
            any(not p or p.startswith('.') or p.endswith(('.', ' ')) or
                any(ord(c) < 32 or c in '\\:*?"<>|' for c in p) or
                p.split('.')[0].upper() in {'CON','PRN','AUX','NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}
                for p in parts)):
        raise ValueError('更新包含无效路径')
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(path)) or path.resolve() != path:
            raise ValueError('更新路径指向部署目录以外')
    return path


def program_path(name):
    p = PurePosixPath(name)
    if name in {'candidate.json','MANIFEST.sha256','启动 Telyincore.sh','启动 TelyinCore.bat'}:
        return True
    if len(p.parts) < 2 or p.parts[0] != 'core':
        return False
    if any(x.lower() in {'data','runtime','extensions','models','services','bots','credentials','node_modules'} or x.startswith('.') for x in p.parts):
        return False
    return p.suffix.lower() in {'.py','.js','.mjs','.ts','.css','.html','.svg','.png','.webp','.wav','.woff2','.bat','.ps1','.sh','.toml','.md','.txt','.yaml','.json'} or p.name in {'LICENSE','THIRD_PARTY_NOTICES'}


def folder(root):
    return safe_file(root, 'runtime/package-updates')


def check_manifest(root, archive, platform, base, version):
    row = json.loads(archive.read('UPDATE.json'))
    if (row.get('schema'),row.get('platform'),row.get('from_version'),row.get('to_version')) != ('core-online-update-v1',platform,base,version):
        raise ValueError('增量包的平台或版本不匹配')
    files = row['files']
    if not isinstance(files,list) or not files or len(files)>12000:
        raise ValueError('增量包文件清单无效')
    seen=set()
    for f in files:
        name=f['path']
        safe_file(root,name)
        if not program_path(name) or name.casefold() in seen:
            raise ValueError('更新包含重复路径或用户数据路径')
        seen.add(name.casefold())
        old,new=f['old_sha256'],f['new_sha256']
        if (old is None and new is None) or any(v is not None and not SUM.fullmatch(v) for v in (old,new)):
            raise ValueError('文件校验值无效')
        if new is not None and sha(archive.read('payload/'+name))!=new:
            raise ValueError('包内程序文件校验失败')
    if 'candidate.json' not in seen:
        raise ValueError('缺少版本登记文件')
    candidate=json.loads(archive.read('payload/candidate.json'))
    if candidate_platform(candidate)!=platform or candidate.get('version')!=version:
        raise ValueError('包内版本登记不符')
    return files


def candidate_platform(record):
    expected = {'linux-core-acceptance-v1':'linux','windows-core-acceptance-v1':'win'}.get(record.get('schema'))
    if not expected or record.get('platform',expected) != expected:
        raise ValueError('无法确认安装平台')
    return expected


def inspect_zip(root, z, platform, base, version):
    entries=z.infolist()
    if len(entries)>12002 or len({i.filename for i in entries})!=len(entries) or sum(i.file_size for i in entries)>2*MAX_BYTES:
        raise ValueError('更新包大小或结构无效')
    return check_manifest(root,z,platform,base,version)


def pending(root):
    p=folder(root)/'pending.json'
    if p.is_file():
        return json.loads(p.read_bytes())
    return None


def stage(checker):
    root=checker.root.resolve()
    state=checker.check(force=True)
    if state['status']!='available':
        raise ValueError('当前没有可安装的新版')
    base,version,platform=state['current'],state['latest'],state['platform']
    name=f'Telyincore_{platform}_{base}_to_{version}_update.zip'
    tag=f'{platform}-{version}'
    repo=checker.repository
    api=f'https://api.github.com/repos/{repo}/releases/tags/{tag}'
    headers={'User-Agent':'TelyinCore-online-update','Accept':'application/vnd.github+json'}
    with urlopen(Request(api,headers=headers),timeout=12) as response:
        release=json.loads(response.read(2*1024*1024))
    if release.get('draft') is not False or release.get('tag_name')!=tag:
        raise ValueError('未取得该平台的公开发布记录')
    url=f'https://github.com/{repo}/releases/download/{tag}/{name}'
    matches=[a for a in release.get('assets',[]) if a.get('name')==name and a.get('browser_download_url')==url and a.get('state')=='uploaded']
    if len(matches)!=1:
        raise ValueError('没有适用于当前版本的增量包')
    asset=matches[0]
    expected=str(asset.get('digest') or '').removeprefix('sha256:')
    if not SUM.fullmatch(expected) or not 0<asset['size']<=MAX_BYTES:
        raise ValueError('GitHub 附件缺少 SHA-256 或大小超限')
    cache=folder(root);cache.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=cache,delete=False) as out:
        temp=Path(out.name)
    try:
        with temp.open('wb') as out, urlopen(Request(url,headers={'User-Agent':headers['User-Agent']}),timeout=25) as response:
            count=0; checksum=hashlib.sha256();deadline=time.monotonic()+180
            while block:=response.read(256*1024):
                count+=len(block)
                if count>asset['size'] or time.monotonic()>deadline:
                    raise ValueError('更新包下载中断或长度异常')
                checksum.update(block);out.write(block)
            out.flush();os.fsync(out.fileno())
        if count!=asset['size'] or checksum.hexdigest()!=expected:
            raise ValueError('下载校验失败，请重新检查更新')
        with zipfile.ZipFile(temp) as z:inspect_zip(root,z,platform,base,version)
        temp.replace(cache/(expected+'.zip'))
        receipt=dict(platform=platform,base=base,version=version,sha256=expected)
        atomic(cache/'pending.json',json.dumps(receipt).encode(),0o600)
        return receipt
    finally:
        temp.unlink(missing_ok=True)


def apply_pending(root, platform):
    root=Path(root).resolve()
    if pending(root) is None:
        return False
    with ExitStack() as stack:
        if platform=='linux':
            import fcntl
            for name in ('.linux-preparation.lock','.linux-acceptance-launch.lock'):
                f=stack.enter_context((root/'core'/name).open('a+b'))
                fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        elif platform=='win':
            from bot_global_admission import HostAdmissionLock
            core=root/'core'
            lock=HostAdmissionLock(core,exclusive=True).acquire()
            stack.callback(lock.close)
            owners=core/'data/host-start-owners'
            if (core/'data/host-maintenance.json').exists() or (owners.exists() and any(owners.iterdir())):
                raise ValueError('请先正常休止此部署的其他 Bot 并结束档案维护')
        else:
            raise ValueError('不支持的平台')
        r=pending(root)
        if r is None:return False
        if r['platform']!=platform or not SUM.fullmatch(r['sha256']):
            raise ValueError('更新登记无效')
        cache=folder(root);archive=cache/(r['sha256']+'.zip')
        with archive.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=r['sha256']:
                raise ValueError('暂存包校验失败')
            with zipfile.ZipFile(stream) as z:
                files=inspect_zip(root,z,platform,r['base'],r['version'])
                local=json.loads((root/'candidate.json').read_bytes())
                if candidate_platform(local)!=platform or local.get('version') not in {r['base'],r['version']}:
                    raise ValueError('已安装版本与增量包不符')
                changes=[]
                for f in files:
                    name=f['path'];target=safe_file(root,name)
                    current=sha(target.read_bytes()) if target.exists() else None
                    if current==f['new_sha256']:continue
                    if current!=f['old_sha256']:
                        raise ValueError(f'本机程序文件已变更，已保留：{name}')
                    changes.append(f)
                # The same verified receipt resumes interrupted writes before any service starts.
                for f in sorted(changes,key=lambda x:(x['path']=='candidate.json',x['path'].endswith('/current.json'))):
                    name=f['path'];target=safe_file(root,name)
                    if f['new_sha256'] is None:
                        target.unlink()
                    else:
                        mode=stat.S_IMODE(target.stat().st_mode) if target.exists() else (0o755 if name.endswith('.sh') else 0o644)
                        atomic(target,z.read('payload/'+name),mode)
        (cache/'pending.json').unlink()
        archive.unlink(missing_ok=True)
        return True


def start_restart(root, port):
    """The verified local worker waits for the original service to leave normally."""
    import subprocess
    import sys
    if not pending(root):
        raise ValueError('请先下载并校验更新包')
    script = root/'core/scripts/online_update.py'
    args = [sys.executable, str(script), '--after-exit', str(os.getpid()), '--port', str(port)]
    log = folder(root)/'restart.log'
    if sys.platform == 'linux' and os.environ.get('INVOCATION_ID') and shutil.which('systemd-run'):
        subprocess.run(['systemd-run', '--user', '--collect', '--quiet',
                        '--property=Type=exec', *args], check=True, timeout=10)
    else:
        options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
                   if os.name == 'nt' else {'start_new_session': True})
        with log.open('ab') as output:
            subprocess.Popen(args, cwd=root, stdin=subprocess.DEVNULL, stdout=output,
                             stderr=subprocess.STDOUT, close_fds=True, **options)

