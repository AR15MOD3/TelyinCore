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
from urllib.request import Request

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


def fetch_asset(url, proxy, path, size, expected, user_agent):
    from .download_sources import open_public
    # Retain an interrupted prefix across mirrors. If its hash poisons the
    # completed file, allow a single clean download from the same origin.
    for attempt in range(2):
        offset = path.stat().st_size
        if offset >= size:
            offset = 0
        headers = {'User-Agent': user_agent, 'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        with open_public(Request(url, headers=headers), 15, proxy) as response:
            if getattr(response, 'status', 200) == 206:
                if response.headers.get('Content-Range') != f'bytes {offset}-{size-1}/{size}':
                    raise ValueError('更新包续传范围不符')
            else:
                offset = 0
            with path.open('ab' if offset else 'wb') as out:
                count = offset
                deadline = time.monotonic() + 180
                while block := response.read(256 * 1024):
                    count += len(block)
                    if count > size or time.monotonic() > deadline:
                        raise ValueError('更新包长度异常或下载超时')
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
        if count != size:
            raise ValueError('更新包下载不完整')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() == expected:
                return
        with path.open('wb'):
            pass
        if not offset or attempt:
            raise ValueError('更新包 SHA-256 校验失败')


def release_asset(api, tag, url, name, headers, proxy):
    from http.client import HTTPException
    from urllib.error import HTTPError
    from .download_sources import github_json, open_public

    proof = None
    try:
        release = github_json(api, headers, proxy, official_only=True)
    except (OSError, HTTPException) as exc:
        if isinstance(exc, HTTPError) and exc.code in (401, 404, 410):
            raise
        # The API may be rate-limited while the release website is reachable.
        # Anchor the checksum on GitHub itself before using mirrored metadata.
        request = Request(url[:-4] + '.sha256', headers={'User-Agent': headers['User-Agent']})
        with open_public(request, 8, proxy) as response:
            raw = response.read(513)
        if len(raw) > 512:
            raise ValueError('官方校验文件过大')
        parts = raw.decode('ascii').split()
        if len(parts) != 2 or not SUM.fullmatch(parts[0]) or parts[1] != name:
            raise ValueError('官方校验文件与目标更新包不符')
        proof = parts[0]
        release = github_json(api, headers, proxy)
    if not isinstance(release, dict) or release.get('draft') is not False or release.get('tag_name') != tag:
        raise ValueError('未取得该平台的公开发布记录')
    matches = [asset for asset in release.get('assets', []) if isinstance(asset, dict)
               and asset.get('name') == name and asset.get('browser_download_url') == url
               and asset.get('state') == 'uploaded' and asset.get('label') == 'core-online-update-v1']
    if len(matches) != 1:
        raise ValueError('没有适用于当前版本的增量包')
    asset = matches[0]
    expected = str(asset.get('digest') or '').removeprefix('sha256:')
    size = asset.get('size')
    if not SUM.fullmatch(expected) or type(size) is not int or not 0 < size <= MAX_BYTES:
        raise ValueError('GitHub 附件缺少 SHA-256 或大小超限')
    if proof is not None and proof != expected:
        raise ValueError('镜像元数据与官方校验文件不符')
    return size, expected


def stage(checker):
    root=checker.root.resolve()
    state=checker.check(force=True)
    if state['status']!='available':
        raise ValueError('当前没有可安装的新版')
    base,version,platform=state['current'],state['latest'],state['platform']
    name=f'Telyincore_{platform}_{base}_to_{version}_update.zip'
    from .package_updates import release_tag
    tag=release_tag(platform, version, checker.channel)
    repo=checker.repository
    api=f'https://api.github.com/repos/{repo}/releases/tags/{tag}'
    headers={'User-Agent':'TelyinCore-online-update','Accept':'application/vnd.github+json'}
    from .download_sources import github_routes, selected_proxy, TRANSFER_ERRORS
    proxy = selected_proxy("official", checker.proxy_root)
    checker.install_progress = "正在向发布方核对附件 SHA-256"
    url = f'https://github.com/{repo}/releases/download/{tag}/{name}'
    size, expected = release_asset(api, tag, url, name, headers, proxy)
    cache=folder(root);cache.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=cache,delete=False) as out:
        temp=Path(out.name)
    try:
        cached = cache / (expected + '.zip')
        reusable = False
        if cached.is_file() and cached.stat().st_size == size:
            with cached.open('rb') as stream:
                reusable = hashlib.file_digest(stream, 'sha256').hexdigest() == expected
        source = 'cache'
        route = 'local'
        if reusable:
            checker.install_progress = '已找到校验通过的本地更新包'
        else:
            for target, via in github_routes(url, proxy):
                is_origin = target == url
                label = '官方源' if is_origin else 'GitHub 镜像'
                checker.install_progress = label + (' · 代理' if via else ' · 直连')
                try:
                    fetch_asset(target, via, temp, size, expected, headers['User-Agent'])
                    source = 'official' if is_origin else 'mirror'
                    route = 'proxy' if via else 'direct'
                    break
                except TRANSFER_ERRORS:
                    if is_origin:
                        raise
        checker.install_progress = '正在核对更新包和本机版本'
        with zipfile.ZipFile(cached if reusable else temp) as z:
            files = inspect_zip(root,z,platform,base,version)
            for item in files:
                path = safe_file(root, item['path'])
                actual = sha(path.read_bytes()) if path.exists() else None
                if actual not in (item['old_sha256'], item['new_sha256']):
                    raise ValueError('本机程序文件与更新包不匹配，已保留：' + item['path'])
        if not reusable:
            temp.replace(cached)
        receipt=dict(platform=platform,base=base,version=version,sha256=expected,source=source,route=route)
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
        source, route = r.get('source', 'offline'), r.get('route', 'local')
        record = dict(platform=platform, base=r['base'], version=r['version'], sha256=r['sha256'],
                      source=source, route=route, installed_at=int(time.time()))
        atomic(cache/'last-install.json', json.dumps(record).encode(), 0o600)
        (cache/'pending.json').unlink()
        archive.unlink(missing_ok=True)
        return True


def start_restart(root, port):
    """The verified local worker waits for the original service to leave normally."""
    import subprocess
    import sys
    receipt = pending(root)
    if not receipt:
        raise ValueError('请先下载并校验更新包')
    script = root/'core/scripts/online_update.py'
    args = [sys.executable, str(script), '--after-exit', str(os.getpid()), '--port', str(port)]
    if os.name == 'nt':
        if '--bot' not in sys.argv:
            raise ValueError('无法确认当前 Bot 档案，更新包已保留')
        from bot_registry import bot_id
        bot = bot_id(sys.argv[sys.argv.index('--bot') + 1])
        record = dict(bot=bot, port=port, sha256=receipt['sha256'])
        atomic(folder(root) / f'restart-{os.getpid()}.json', json.dumps(record).encode(), 0o600)
        return  # The owning launcher relaunches after retiring the child and its job.
    log = folder(root)/'restart.log'
    if sys.platform == 'linux' and os.environ.get('INVOCATION_ID') and shutil.which('systemd-run'):
        subprocess.run(['systemd-run', '--user', '--collect', '--quiet',
                        '--property=Type=exec', *args], check=True, timeout=10)
    else:
        with log.open('ab') as output:
            subprocess.Popen(args, cwd=root, stdin=subprocess.DEVNULL, stdout=output,
                             stderr=subprocess.STDOUT, close_fds=True, start_new_session=True)


def requested_windows_restart(root, pid, bot, port):
    path = folder(root) / f'restart-{pid}.json'
    if not path.is_file():
        return False
    try:
        receipt = pending(root)
        return bool(receipt and json.loads(path.read_bytes()) == {
            'bot': bot, 'port': port, 'sha256': receipt['sha256']})
    finally:
        path.unlink(missing_ok=True)
