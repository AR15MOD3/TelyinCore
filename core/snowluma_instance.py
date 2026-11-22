"""One fixed SnowLuma runtime per Bot; shared executable, private state and ports.

No process launch, login, injection, message or stale-custody deletion. Initialization
is a guarded maintenance transaction. Interrupted transactions remain visibly denied.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import uuid

from bot_private_file import open_private_new
from runtime_identity import identity_lock, safe_path


def _bytes(path, limit=262144):
    if not path.exists(): return None
    with path.open('rb') as f:
        info = os.fstat(f.fileno()); raw = f.read(limit+1)
    if len(raw)>limit or info.st_nlink!=1 or not stat.S_ISREG(info.st_mode):
        raise ValueError('bounded private SnowLuma file required')
    return raw


def _json(raw):
    def unique(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('duplicate SnowLuma metadata')
            result[key]=value
        return result
    def invalid_constant(value): raise ValueError('non-finite SnowLuma metadata')
    value=json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    if type(value) is not dict: raise ValueError('SnowLuma object required')
    return value


def _encode(value): return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()
def _sha(raw): return None if raw is None else hashlib.sha256(raw).hexdigest()


def _write_new(path, raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    with open_private_new(path) as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())


def _free(port):
    with socket.socket() as s:
        if os.name=='nt': s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try: s.bind(('127.0.0.1',port))
        except OSError: return False
        return True


@dataclass(frozen=True)
class SnowInstance:
    core: Path
    bot: str
    root: Path
    webui: int
    http: int
    ws: int

    @property
    def management_url(self): return f'http://127.0.0.1:{self.webui}'

    @property
    def ports(self): return (self.webui,self.http,self.ws)

    def environment(self):
        return dict(SNOWLUMA_WEBUI_HOST='127.0.0.1',SNOWLUMA_WEBUI_PORT=str(self.webui),
            SNOWLUMA_ONEBOT_HTTP_PORT=str(self.http),SNOWLUMA_ONEBOT_WS_PORT=str(self.ws),
            SNOWLUMA_LOG_DIR=str(self.root/'logs'),SNOWLUMA_HOOK_AUTOLOAD='0',
            SNOWLUMA_MANUAL_ATTACH_ONLY='1',
            SNOWLUMA_UPDATE_CHECK='off')


def instance_root(core, bot):
    from bot_registry import bot_id
    core=Path(core).resolve(); bot=bot_id(bot)
    return safe_path(core.parent,core.parent/'bots'/bot/'infinity/snowluma')


def load_instance(core, bot):
    core=Path(core).resolve(); root=instance_root(core,bot)
    if (root/'.migration-pending.json').exists(): raise ValueError('SnowLuma迁移未完成；保留原数据，禁止启动')
    raw=_bytes(safe_path(core.parent,root/'instance.json'),16384)
    if raw is None: return None
    value=_json(raw)
    if (set(value)!={'version','bot','ports'} or type(value['version']) is not int or value['version']!=1 or value['bot']!=bot
            or type(value['ports']) is not list or len(value['ports'])!=3
            or any(type(p) is not int or not 1024<=p<=65535 for p in value['ports'])
            or len(set(value['ports']))!=3): raise ValueError('SnowLuma实例登记无效')
    return SnowInstance(core,bot,root,*value['ports'])


def _allocations(core):
    path=safe_path(core.parent,core/'data/snowluma-port-allocations.json')
    raw=_bytes(path,131072)
    value={'version':1,'bots':{}} if raw is None else _json(raw)
    if set(value)!={'version','bots'} or type(value['version']) is not int or value['version']!=1 or type(value['bots']) is not dict:
        raise ValueError('SnowLuma端口登记无效')
    from bot_registry import bot_id
    used=set()
    for bot,ports in value['bots'].items():
        bot_id(bot)
        if (type(ports) is not list or len(ports)!=3 or any(type(p) is not int or not 1024<=p<=65535 for p in ports)
                or len(set(ports))!=3 or used.intersection(ports)): raise ValueError('SnowLuma端口登记冲突')
        used.update(ports)
    return path,raw,value


def _commit(core, root, files, witnesses=()):
    """CAS per file under the global identity lock; partial commit blocks this Bot."""
    nonce=uuid.uuid4().hex
    def stable_sources():
        if any(_bytes(p)!=raw for p,raw in witnesses): raise ValueError('SnowLuma迁移源并发变化；保留待核验状态')
    stable_sources()
    journal=safe_path(core.parent,root/'.migration-pending.json')
    _write_new(journal,_encode({'version':1,'transaction':nonce,
        'files':[{'path':str(p.relative_to(core.parent)),'before':_sha(old),'after':_sha(new)} for p,old,new in files]}))
    backups=root/'migration-backups'/nonce
    stages=[]
    for i,(path,old,new) in enumerate(files):
        path=safe_path(core.parent,path)
        if _bytes(path)!=old: raise ValueError('SnowLuma迁移源并发变化；未覆盖')
        if old is not None: _write_new(backups/f'{i}.bak',old)
        stage=safe_path(core.parent,path.with_name(path.name+'.'+nonce+'.pending'))
        _write_new(stage,new); stages.append(stage)
    for (path,old,new),stage in zip(files,stages):
        stable_sources()
        if _bytes(path)!=old: raise ValueError('SnowLuma迁移目标并发变化；未覆盖')
        if old is None:
            os.link(stage,path)  # create-only, never overwrite a concurrent creator
            stage.unlink()
        else: os.replace(stage,path)
        if _bytes(path)!=new: raise ValueError('SnowLuma迁移回读不一致')
    journal.unlink()


def _set_aside_imported_runtime(core, bot, allocations):
    """Preserve foreign machine-specific data without acting on its records."""
    from runtime_identity import MARKER, _object, bindings, check_layout

    parent = core.parent / 'bots'
    folder = safe_path(parent, parent / bot)
    marker = safe_path(parent, folder / MARKER)
    base = core / 'data'
    if not marker.is_file() or bot in bindings(base):
        return
    check_layout(base, parent, selected=bot)
    provenance = _object(marker)
    archive = safe_path(parent, folder / '.imported-runtime' / provenance['token'])
    if bot in allocations:
        if archive.is_dir():
            return  # An earlier attempt already initialized local endpoints.
        raise ValueError('导入档案的 SnowLuma 端口已有本机登记；请先核对归属')
    archive.mkdir(parents=True, exist_ok=True)
    for relative in (
        'infinity/snowluma',
        'infinity/qq/config.json',
        'infinity/processes/snowluma-recovery.json',
        'infinity/processes/snowluma-recovery.json.login-attempts',
        'infinity/processes/snowluma-recovery.json.process-attempts',
    ):
        source = safe_path(parent, folder / relative)
        if not source.exists():
            continue
        destination = safe_path(parent, archive / relative)
        if destination.exists():
            raise ValueError('导入档案的新旧运行记录同时存在；请先核对归属')
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)


def initialize_instance(core, bot, *, baseline, available=_free, exclude_ports=()):
    """Allocate independent endpoints before plugin load. Never start SnowLuma.

    Existing Bot admin auth/consent are preserved from the old common deployment;
    only its own per-account OneBot config is imported, never the whole directory.
    """
    from service_layout import snowluma_directory
    from snowluma_connection import connection_settings
    from urllib.parse import urlsplit
    core=Path(core).resolve(); root=instance_root(core,bot)
    if type(baseline) is not bool: raise ValueError('fixed baseline scope required')
    if any(type(p) is not int or not 1<=p<=65535 for p in exclude_ports): raise ValueError('fixed Core ports required')
    with identity_lock(core/'data'):
        # Old single-instance custody must be retired by its original live HOST.
        legacy=safe_path(core.parent,core/'data/snowluma-companion-custody.json')
        if legacy.exists(): raise ValueError('旧共享SnowLuma尚未完成原HOST退役；未迁移或另起实例')
        allocation_path,allocation_raw,allocations=_allocations(core)
        _set_aside_imported_runtime(core,bot,allocations['bots'])
        existing=load_instance(core,bot)
        if existing:
            if allocations['bots'].get(bot)!=list(existing.ports): raise ValueError('SnowLuma实例与端口登记不一致')
            custody=root/'companion-custody.json'
            if custody.exists() or all(available(p) for p in existing.ports): return existing
        elif root.exists(): raise ValueError('SnowLuma实例目录已有未确认数据；不覆盖')
        scope=core.parent/'bots'/bot/'infinity'
        qq=safe_path(core.parent,core/'extensions/core-infinity-qq/config.json' if baseline else scope/'qq/config.json')
        from service_layout import qq_recovery_file
        recovery=qq_recovery_file(core,bot,baseline=baseline)
        qq_raw=_bytes(qq); config={} if qq_raw is None else _json(qq_raw)
        recovery_raw=_bytes(recovery)
        witnesses=[(recovery,recovery_raw)]
        target=str((_json(recovery_raw or b'{}')).get('targetUin') or '')
        if target and (not target.isascii() or not target.isdecimal()): raise ValueError('目标QQ身份无效')
        http=urlsplit(str(config.get('httpUrl') or 'http://127.0.0.1:3000'))
        ws=urlsplit(str(config.get('wsUrl') or 'ws://127.0.0.1:3001'))
        managed_connection=(http.hostname=='127.0.0.1' and ws.hostname=='127.0.0.1'
            and http.scheme=='http' and ws.scheme=='ws')
        used={p for name,ports in allocations['bots'].items() if name!=bot for p in ports}
        used.update(exclude_ports)
        def pick(start):
            for p in range(max(1024,start),65536):
                if p not in used and available(p): used.add(p); return p
            raise ValueError('没有可用的SnowLuma端口')
        ports=[pick(existing.webui if existing else 5099),
            pick((http.port or 3000) if managed_connection else 3000),
            pick((ws.port or 3001) if managed_connection else 3001)]
        plan=SnowInstance(core,bot,root,*ports)
        source_dir=snowluma_directory(core)/'config'
        own_name=f'onebot_{target}.json' if target else 'onebot.json'
        own=safe_path(core.parent,(root/'config'/own_name) if existing else source_dir/own_name)
        own_raw=_bytes(own)
        if not existing:witnesses.append((own,own_raw))
        if own_raw:
            onebot=_json(own_raw)
            settings=connection_settings(onebot)
            http_mismatch=config.get('token') and settings['token']!=config['token']
            ws_mismatch=(config.get('wsToken',config.get('token'))
                and settings['wsToken']!=config.get('wsToken',config.get('token')))
            if managed_connection and (http_mismatch or ws_mismatch):
                raise ValueError('本Bot与原账号连接凭据不一致；未复制其他账号或覆盖')
        else:
            if target and qq_raw is not None and managed_connection: raise ValueError('缺少本Bot原账号配置；不按其他账号补齐')
            token=(config.get('token') if managed_connection else None) or secrets.token_urlsafe(32)
            onebot={'networks':{'httpServers':[{'host':'127.0.0.1','port':3000,'path':'/','accessToken':token,'messageFormat':'array'}],
                'wsServers':[{'host':'127.0.0.1','port':3001,'path':'/','role':'Universal','accessToken':(config.get('wsToken') if managed_connection else None) or token,'messageFormat':'array'}],
                'httpClients':[],'wsClients':[]},'historySync':{'enabled':False}}
        for kind,port in [('httpServers',plan.http),('wsServers',plan.ws)]:
            active=[n for n in onebot['networks'][kind] if n.get('enabled',True) is not False]
            if len(active)!=1: raise ValueError('本账号需要唯一的HTTP与WS监听')
            active[0].update(host='127.0.0.1',port=port)
        if managed_connection:config.update(connection_settings(onebot))
        allocations['bots'][bot]=ports
        files=[]
        if not existing:
            root.mkdir(parents=True,exist_ok=False)
            for name in ('webui.json','consent.json'):
                p=safe_path(core.parent,source_dir/name); raw=_bytes(p)
                witnesses.append((p,raw))
                if raw is not None: files.append((root/'config'/name,None,raw))
        destination=root/'config'/own_name
        files += [(destination,_bytes(destination),_encode(onebot))]
        if managed_connection:files.append((qq,qq_raw,_encode(config)))
        files += [
            (allocation_path,allocation_raw,_encode(allocations)),
            (root/'instance.json',_bytes(root/'instance.json'),_encode({'version':1,'bot':bot,'ports':ports}))]
        _commit(core,root,files,witnesses)
        return load_instance(core,bot)


def assert_spawn_available(instance):
    from service_layout import snowluma_directory
    entry=snowluma_directory(instance.core)/'index.mjs'
    capabilities=entry.read_bytes()
    if b'CORE_BOT_ISOLATION_V1' not in capabilities or b'CORE_OBSERVE_REFRESH_V1' not in capabilities:
        raise ValueError('当前SnowLuma程序缺少独立账号接入支持；未启动，不回退共享进程')
    if load_instance(instance.core,instance.bot)!=instance: raise ValueError('SnowLuma实例在启动前改变')
    _,_,allocations=_allocations(instance.core)
    if allocations['bots'].get(instance.bot)!=list(instance.ports): raise ValueError('SnowLuma端口登记在启动前改变')
    if not all(_free(p) for p in instance.ports):
        raise ValueError('SnowLuma端口在启动前被占用；未接入其他Bot，请正常重开当前Bot重新分配')
