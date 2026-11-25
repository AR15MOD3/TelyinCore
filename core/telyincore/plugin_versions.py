"""Explicit GitHub release selection. Never executes code during discovery/install."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
from urllib.parse import quote
import uuid

from .plugin_updates import read_json, repository, Version
from .plugin_package_lock import PackageLock

_REPO = re.compile(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+\Z')
_cache = {}
_guard = threading.Lock()


def checked_repo(repo):
    if not isinstance(repo, str) or len(repo) > 200 or not _REPO.fullmatch(repo) or repository('https://github.com/'+repo) != repo:
        raise ValueError('GitHub 仓库地址无效')
    return repo


def bundled(root, name):
    from runtime_identity import safe_path
    for path in (Path(root)/'builtin/infinity').glob('*/package.json'):
        if read_json(safe_path(Path(root), path)).get('name') == name:
            return path.parent
    return None


def _ask(root, repo, path, *, official=False, network_root=None):
    from .download_sources import github_json, public_network, settings_root
    return github_json(f'https://api.github.com/repos/{checked_repo(repo)}/{path}',
        {'User-Agent':'TelyinCore-plugin-versions','Accept':'application/vnd.github+json'},
        public_network(settings_root(root) if network_root is None else network_root, 'market'), limit=2*1024*1024, timeout=12,
        official_only=official)


def history(root, repo, page=1, kind='releases'):
    checked_repo(repo)
    if type(page) is not int or not 1 <= page <= 1000 or kind not in ('releases','tags'):
        raise ValueError('版本分页参数无效')
    key=(str(Path(root).resolve()),repo.lower(),page,kind)
    with _guard:
        cached=_cache.get(key)
        if cached and time.monotonic()-cached[0]<300: return cached[1]
    data=_ask(root,repo,f'{kind}?per_page=20&page={page}')
    if not isinstance(data,list): raise ValueError('GitHub 版本列表格式错误')
    rows=[]
    for row in data:
        if not isinstance(row,dict) or row.get('draft'): continue
        tag=row.get('tag_name' if kind=='releases' else 'name')
        if not isinstance(tag,str) or not tag or len(tag)>200 or any(ord(c)<32 for c in tag): continue
        tag_url=quote(tag,safe='')
        rows.append({'tag':tag,'title':str(row.get('name') or tag)[:200],
            'date':row.get('published_at') if kind=='releases' else None,
            'prerelease':row.get('prerelease') is True,
            'notes':str(row.get('body') or '')[:12000],
            'source':f'https://github.com/{repo}/'+('releases/tag/' if kind=='releases' else 'tree/')+tag_url,
            'download':f'https://github.com/{repo}/archive/refs/tags/{tag_url}.tar.gz'})
    result={'rows':rows,'page':page,'more':len(data)==20,'kind':kind}
    with _guard:
        if len(_cache)>=128: _cache.pop(next(iter(_cache)))
        _cache[key]=(time.monotonic(),result)
    return result


def _copy_data(core, old, stage):
    """Preserve private data but never resurrect old executable files."""
    from runtime_identity import safe_path
    for path in old.rglob('*'):
        path=safe_path(core,path)
        if not path.is_file(): continue
        rel=path.relative_to(old)
        if ('node_modules' in rel.parts or '__pycache__' in rel.parts
                or path.suffix.lower() in {'.py','.pyc','.pyd','.js','.mjs','.cjs','.ts','.dll','.exe','.so','.sh','.bat','.cmd','.ps1','.vbs','.wasm'}
                or path.name in {'package.json','package-lock.json','pnpm-lock.yaml'}): continue
        target=stage/rel
        if target.exists() and path.name not in {'config.json','settings.json','.env'}: continue
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)


def publish_version(core, name, stage, destination, current, *, ready=lambda:None, reason='version-change'):
    """Lease protects live runtimes; retain the entire old package and roll back rename failures."""
    from runtime_identity import safe_path
    from .plugin_scope_store import plugin_name
    from .plugin_uninstall import publish_package
    core=Path(core).resolve();name=plugin_name(name)
    destination=safe_path(core,destination)
    with PackageLock(core,'global-uninstall',exclusive=True,operation=True):
        # Check inside the lease, so no runtime can start between check and publish.
        with PackageLock(core,name,exclusive=True):
            ready()
            if not destination.exists():
                if current: raise ValueError('安装状态已变化，请刷新后重试')
                # No old package exists; restoration is handled by the common installer below.
            else:
                old=read_json(destination/'package.json')
                if not current or old.get('version')!=current or old.get('name')!=name:
                    raise ValueError('已安装版本发生变化，请刷新后重试')
                _copy_data(core,destination,stage)
                backup=safe_path(core,core/'data/plugin-retained'/name/(str(time.time_ns())+'-'+uuid.uuid4().hex[:8]))
                backup.mkdir(parents=True)
                (backup/'receipt.json').write_text(json.dumps({'schema':1,'plugin':name,'reason':reason,'version':current}),encoding='utf-8')
                os.replace(destination,backup/'package')
                try: os.replace(stage,destination)
                except BaseException:
                    os.replace(backup/'package',destination)
                    raise
                return
        publish_package(core,name,stage,destination)


def install_version(root, repo, tag, name, current='', *, proxy_root=None, ready=lambda:None):
    from .market import install_url, validate_plugin_name
    root=Path(root).resolve();checked_repo(repo);validate_plugin_name(name)
    if bundled(root,name): raise ValueError('Core 内置插件随 Core 更新，不支持从独立仓库切换版本')
    if not isinstance(tag,str) or not tag or len(tag)>200 or any(ord(c)<32 for c in tag): raise ValueError('版本标签无效')
    destination=root/'extensions'/name
    def check_source():
        if destination.exists():
            from runtime_identity import safe_path
            spec=read_json(safe_path(root,destination/'package.json'))
            origins=read_json(root/'extensions/.origins.json') if (root/'extensions/.origins.json').exists() else {}
            declared=repository(spec.get('repository'));recorded=repository(origins.get(name,{}).get('url'))
            if not (declared or recorded) or any(value and value.lower()!=repo.lower() for value in (declared,recorded)):
                raise ValueError('所选仓库与已安装来源不一致，不能覆盖')
    check_source()
    # Resolve only a tag, not a same-named branch. Pin the install to that commit.
    commit=_ask(root,repo,'commits/'+quote('refs/tags/'+tag,safe=''),official=True,network_root=proxy_root)
    sha=commit.get('sha') if isinstance(commit,dict) else None
    if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{40}',sha): raise ValueError('无法核验所选标签对应的提交')
    url=f'https://github.com/{repo}/archive/{sha}.tar.gz'
    def publish(core,n,stage,dest):
        Version(read_json(stage/'package.json').get('version'))
        def checked_ready():
            check_source();ready()
        publish_version(core,n,stage,dest,current,ready=checked_ready)
    result=install_url(root,url,proxy_root=proxy_root,expected_name=name,publisher=publish)
    if result.get('ok'):
        result.update(tag=tag,commit=sha,version=read_json(destination/'package.json').get('version'),
            said='所选版本已安装，插件保持停用；请核对配置后启用。')
    return result
