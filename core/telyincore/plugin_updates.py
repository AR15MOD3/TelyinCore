"""Shared, read-only plugin update discovery. Never imports or installs remote code."""
from __future__ import annotations
import base64
from functools import total_ordering
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.error import HTTPError


TTL = 6 * 3600
RETRY = 1800
BATCH = 8
HOURLY_LIMIT = 40  # Reserve anonymous GitHub requests for Core's other features.
_VERSION = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z')
_REPO = re.compile(r'https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9_.-]+)(?:/.*)?\Z')

@total_ordering
class Version:
    def __init__(self, value):
        match = _VERSION.fullmatch(value) if isinstance(value, str) and len(value) < 128 else None
        if not match: raise ValueError('版本号不是 SemVer')
        self.core = tuple(map(int, match.group(1, 2, 3)))
        self.pre = match[4].split('.') if match[4] else []
        if any(x.isdigit() and len(x)>1 and x[0]=='0' for x in self.pre): raise ValueError('预发布版本无效')
    def __eq__(self, other): return (self.core,self.pre)==(other.core,other.pre)
    def __lt__(self, other):
        if self.core != other.core: return self.core < other.core
        if not self.pre or not other.pre: return bool(self.pre) and not other.pre
        for a,b in zip(self.pre,other.pre):
            if a==b: continue
            if a.isdigit() and b.isdigit(): return int(a)<int(b)
            if a.isdigit()!=b.isdigit(): return a.isdigit()
            return a<b
        return len(self.pre)<len(other.pre)

def repository(value):
    if isinstance(value,dict): value=value.get('url')
    if not isinstance(value,str): return ''
    value=value.removeprefix('git+')
    match=_REPO.fullmatch(value)
    if not match: return ''
    repo=match[1].removesuffix('.git')
    return repo if all(part not in ('','.','..') for part in repo.split('/')) else ''

def read_json(path, limit=65536):
    with path.open('rb') as stream: raw=stream.read(limit+1)
    if len(raw)>limit: raise ValueError('元数据过大')
    result=json.loads(raw)
    if not isinstance(result,dict): raise ValueError('元数据格式错误')
    return result

def remote_manifest(repo, root):
    from .download_sources import github_json, public_network, settings_root
    from .plugin_releases import latest_release
    from urllib.parse import quote
    def ask(path):
        return github_json(f'https://api.github.com/repos/{repo}/{path}',
            {'User-Agent':'TelyinCore-plugin-updates','Accept':'application/vnd.github+json'},
            public_network(settings_root(root),'market'),limit=2*1024*1024,timeout=8,
            official_only=True)
    release=latest_release(repo,ask)
    if release is None: return {'release_status':'none'}
    commit=ask('commits/'+quote('refs/tags/'+release['tag'],safe=''))
    sha=commit.get('sha') if isinstance(commit,dict) else None
    if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{40}',sha):
        raise ValueError('无法核验正式版本提交')
    result=ask('contents/package.json?ref='+sha)
    if (not isinstance(result,dict) or result.get('type')!='file' or result.get('encoding')!='base64'
            or type(result.get('size')) is not int or not 0<result['size']<=65536):
        raise ValueError('仓库根目录没有合规的插件清单')
    raw=base64.b64decode(''.join(result.get('content','').split()),validate=True)
    if len(raw)!=result['size']: raise ValueError('清单长度不匹配')
    spec=json.loads(raw)
    if not isinstance(spec,dict): raise ValueError('插件清单格式错误')
    declaration=spec.get('telyincore',{})
    if (not isinstance(declaration,dict) or declaration.get('kind')!='infinity'
            or type(declaration.get('api')) is not int or declaration['api']!=1
            or 'core-infinity' not in spec.get('keywords',[])):
        raise ValueError('远端插件契约不兼容')
    Version(spec.get('version'))
    if not isinstance(spec.get('name'),str) or len(spec['name'])>200: raise ValueError('远端插件名称无效')
    if spec['version'] != release['version']: raise ValueError('正式版本标签与插件清单版本不一致')
    return {'name':spec['name'],'version':spec['version'],'tag':release['tag'],'release_status':'stable'}

class PluginUpdates:
    def __init__(self,root,fetch=None,clock=time.time):
        self.root=Path(root).resolve();self.fetch=fetch or (lambda repo:remote_manifest(repo,self.root));self.clock=clock
        self._guard=threading.Lock();self._requested=threading.Event();self._force=False;self._thread=None;self._running=False
        self._error='';self._refresh_since=0;self._servicing=False
    def path(self):
        from runtime_identity import safe_path
        return safe_path(self.root,self.root/'data/plugin-updates/state.json')
    def _state(self):
        try:
            state=read_json(self.path(),2*1024*1024)
            if state.get('schema')!=2 or not isinstance(state.get('cache'),dict) or not isinstance(state.get('attempts'),list): raise ValueError()
            return state
        except (OSError,ValueError): return {'schema':2,'cache':{},'attempts':[]}
    def _save(self,state):
        path=self.path();path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as file:
            tmp=Path(file.name);json.dump(state,file,ensure_ascii=False);file.flush();os.fsync(file.fileno())
        try: os.replace(tmp,path)
        finally: tmp.unlink(missing_ok=True)
    def history(self, repo, page=1, kind="releases"):
        from .plugin_versions import history
        return history(self.root, repo, page, kind)
    def inventory(self):
        from runtime_identity import safe_path
        home=safe_path(self.root,self.root/'extensions')
        try: origins=read_json(safe_path(self.root,home/'.origins.json'))
        except (OSError,ValueError): origins={}
        builtins=set()
        from .plugin_paths import source_root
        source=source_root(self.root)
        for file in (source/'builtin/infinity').glob('*/package.json'):
            try: builtins.add(read_json(safe_path(source,file)).get('name'))
            except (OSError,ValueError): pass
        rows=[]
        for folder in sorted(home.iterdir()) if home.exists() else []:
            if folder.name.startswith('.') or not folder.is_dir(): continue
            try:
                spec=read_json(safe_path(self.root,folder/'package.json'))
                name=spec.get('name')
                if name!=folder.name: raise ValueError('插件目录与名称不一致')
                contract=spec.get('telyincore',{})
                if not isinstance(contract,dict): contract={}
                origin=origins.get(name,{})
                recorded=repository(origin.get('url')) if isinstance(origin,dict) else ''
                declared=repository(spec.get('repository'))
                repo=recorded or declared
                row={'name':name,'displayName':contract.get('displayName') or name,
                     'current':spec.get('version',''),'repository':repo,'status':'pending'}
                if name in builtins: row.update(status='managed',message='随 Core 更新',repository='')
                elif recorded and declared and recorded.lower()!=declared.lower(): row.update(status='unsupported',message='安装来源与声明仓库不一致，需手动核对')
                elif not repo: row.update(status='unsupported',message='未声明可检查的 GitHub 仓库')
                else: Version(row['current'])
                rows.append(row)
            except (OSError,ValueError,TypeError):
                rows.append({'name':folder.name,'displayName':folder.name,'current':'','repository':'','status':'unsupported','message':'本机清单或版本无法识别'})
        return rows
    def read(self,refresh=False):
        with self._guard:
            if refresh:
                self._force=True
                self._refresh_since=self.clock()
                self._requested.set()
            checking=self._force or self._servicing or self._running
        state=self._state();now=self.clock();rows=self.inventory()
        for row in rows:
            if row['status']!='pending': continue
            cached=state['cache'].get(row['repository'].lower(),{})
            if not isinstance(cached,dict): continue
            try:
                checked=float(cached.get('checkedAt',0));last=float(cached.get('attemptAt',0))
                row.update(checkedAt=checked or None,attemptAt=last or None,
                    stale=not 0<=now-checked<TTL,error=str(cached.get('error',''))[:200])
                if cached.get('release_status')=='none':
                    row.update(status='unreleased',message='暂无正式发布')
                    continue
                if cached.get('name')!=row['name']:
                    row.update(status='unavailable' if cached else 'pending',message=row['error'] or '远端清单名称未确认')
                    continue
                local=Version(row['current']);remote=Version(cached['version'])
                row['latest']=cached['version']
                if cached.get('tag'): row['tag']=cached['tag']
                status='available' if local<remote else 'ahead' if remote<local else 'current'
                if remote.pre and not local.pre: status='prerelease'
                row.update(status=status,message='')
                if row['error']: row['stale']=True
            except (ValueError,TypeError,KeyError): row.update(status='unavailable',message='缓存不可用，等待重新检查')
        return {'rows':rows,'checking':checking,'message':self._error,
            'available':sum(r['status']=='available' for r in rows),'intervalHours':6,
            'policy':'只检查并提示；不会自动安装、停用插件或重启 Bot。'}
    def check_batch(self,force=False,stopped=None):
        from .plugin_package_lock import PackageLock
        with PackageLock(self.root,'plugin-update-check',exclusive=True,operation=True):
            self._running=True
            try:
                state=self._state();now=self.clock()
                attempts=[t for t in state['attempts'] if type(t) in (int,float) and 0<=now-t<3600]
                repos={r['repository'].lower():r['repository'] for r in self.inventory() if r['status']=='pending'}
                previous_cache=state['cache']
                state['cache']={k:v for k,v in previous_cache.items() if k in repos and isinstance(v,dict)}
                if state['cache'] != previous_cache: self._save(state)
                due=[]
                for key,repo in repos.items():
                    old=state['cache'].get(key,{})
                    try: stamp=float(old.get('attemptAt',0));age=now-stamp
                    except (ValueError,TypeError): stamp=0;age=TTL+1
                    delay=60 if force or stamp<self._refresh_since else RETRY if old.get('error') else TTL
                    if not old or age<0 or age>=delay: due.append((stamp,key,repo))
                for _,key,repo in sorted(due)[:BATCH]:
                    if stopped is not None and stopped.is_set(): break
                    if len(attempts)>=HOURLY_LIMIT:
                        self._error='本小时检查额度已用完，未检查项会在后台继续；这不代表已是最新。';break
                    now=self.clock();attempts.append(now);state['attempts']=attempts
                    old=state['cache'].get(key,{});state['cache'][key]={**old,'attemptAt':now}
                    self._save(state) # Shared request budget survives process exit.
                    try:
                        remote=self.fetch(repo)
                        if remote.get('release_status')!='none':
                            Version(remote['version'])
                            if not isinstance(remote['name'],str): raise ValueError('清单名称无效')
                        state['cache'][key]={**remote,'attemptAt':now,'checkedAt':self.clock(),'error':''}
                        self._error=''
                    except Exception as exc:
                        if isinstance(exc,HTTPError) and exc.code in (403,429):
                            error='GitHub 暂时限流，稍后自动重试'
                            attempts.extend([now]*(HOURLY_LIMIT-len(attempts)))
                        else: error='暂时无法核对远端版本，请检查网络或仓库清单'
                        state['cache'][key]={**old,'attemptAt':now,'error':error}
                    state['attempts']=attempts;self._save(state)
            finally: self._running=False
    def warm(self,stopped,idle=lambda:True):
        if self._thread is not None: return
        def monitor():
            while not stopped.is_set():
                with self._guard:
                    force=self._force
                    self._force=False
                    self._servicing=force
                try:
                    if force or idle(): self.check_batch(force,stopped)
                except (OSError,ValueError): self._error='另一实例正在检查，或本机检查记录暂不可用'
                finally:
                    with self._guard: self._servicing=False
                for _ in range(60):
                    if stopped.wait(1): return
                    if self._requested.is_set():
                        self._requested.clear();break
        self._thread=threading.Thread(target=monitor,name='plugin-update-check',daemon=True);self._thread.start()

plugin_updates=PluginUpdates(Path(__file__).resolve().parents[1])
