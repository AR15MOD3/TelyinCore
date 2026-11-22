"""Offline profile rename using formal-entry exclusion and durable phase records.

This is a cooperating local filesystem transaction, not the experimental live
HostEvidenceRegistry coordinator. It neither adopts nor kills external processes.
Unknown active account/process resources are refused before publishing intent.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import uuid

from bot_lifecycle import BotLifecycle, tree
from bot_registry import bot_id, BotRegistry
from bot_migration_gate import GATE, RETIRED, retired_names
from runtime_identity import safe_path, _object, bindings, LEDGER, MARKER, _check_layout_fenced
from bot_private_file import open_private_new

KIND = 'bot-offline-rename-v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def store(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.rename-' + uuid.uuid4().hex)
    try:
        with open_private_new(temp) as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _raw(path):
    if not path.exists(): return None
    if path.stat().st_size > 65536: raise ValueError('改名控制记录过大，未处理')
    return path.read_bytes()


def _pack(raw):
    return None if raw is None else base64.b64encode(raw).decode('ascii')


def _unpack(raw):
    return None if raw is None else base64.b64decode(raw, validate=True)


class OfflineRename:
    def __init__(self, core):
        self.progress = lambda message: None
        self.service = BotLifecycle(core)
        self.core = self.service.core
        self.base = self.service.base
        self.root = self.service.root

    def isolated(self):
        marker = self.root/'acceptance.json'
        if self.root.name != '.linux-acceptance' or not marker.exists():
            return None
        row = _object(safe_path(self.root,marker))
        if (set(row) != {'version','mode','source_core','bot'} or row['version'] != 1
                or row['mode'] != 'linux-profile-acceptance'
                or row['source_core'] != str(self.root.parent.resolve())):
            raise ValueError('Linux 隔离档案归属不匹配')
        bot_id(row['bot'])
        return row

    def check_layout(self):
        isolated = self.isolated()
        if isolated is None:
            _check_layout_fenced(self.base,self.service.bots)
        else:
            folder = self.path('bots/'+isolated['bot'])
            if not BotRegistry(self.service.bots,Path(),isolated['bot']).valid(folder):
                raise ValueError('Linux 已选档案不完整')
            rows = bindings(self.base)
            if rows and (set(rows) != {isolated['bot']} or
                         _object(folder/MARKER) != {'name':isolated['bot'],'token':rows[isolated['bot']]}):
                raise ValueError('Linux 档案绑定不匹配')

    def path(self, relative):
        return safe_path(self.root, self.root / relative)

    def _external_clear(self, old, baseline):
        # Directory movement is not proof that a separately owned QQ/voice
        # process or account has retired. Preserve its receipts and refuse.
        candidates = [self.base/'snowluma-companion-custody.json',
                      self.base/'parent-account-admissions',
                      self.base/'qq-configured-account-reservations',
                      self.base/'default-account-revocations']
        process = (self.base/'infinity-processes' if old == baseline else
                   self.root/'bots'/old/'infinity/processes')
        candidates.append(process)
        for p in candidates:
            safe_path(self.root, p)
            if p.is_file() or (p.is_dir() and any(x.name != '.bot-identity.lock' for x in p.iterdir())):
                raise ValueError('仍有账号或外部进程归属记录，需先完成其正规退出与核对；未改名：' + p.name)

    def plan(self, old, new):
        old, new = bot_id(old), bot_id(new)
        if old.startswith('.') or new.startswith('.') or old.casefold() == new.casefold():
            raise ValueError('新旧档案名称必须不同；暂不支持仅大小写变化')
        from runtime_identity import check_layout, reserved_names
        isolated = self.isolated()
        if isolated is None: check_layout(self.base, self.service.bots)
        else:
            self.check_layout()
            if isolated['bot'] != old: raise ValueError('只能修改当前 Linux 隔离档案名称')
        if not BotRegistry(self.service.bots, Path(), old).valid(self.path('bots/'+old)):
            raise ValueError('请选择在库且完整的档案；已归档档案请先恢复')
        if old not in [p.name for p in self.service.bots.iterdir()]:
            raise ValueError('原档案名称大小写不匹配')
        taken = reserved_names(self.base) | retired_names(self.base) | {p.name for p in self.service.bots.iterdir()}
        if new.casefold() in {n.casefold() for n in taken}:
            raise ValueError('新名称已存在或曾被保留，请换一个名称')
        owner = _object(self.base/'bot-runtime-owner.json')
        baseline = bot_id(owner['baseline'])
        self._external_clear(old, baseline)
        rows = bindings(self.base)
        runtime = 'core/data' if old == baseline or isolated is not None else 'core/data/bot-runtimes/'+old
        target = runtime if old == baseline or isolated is not None else 'core/data/bot-runtimes/'+new
        moves = [('bots/'+old, 'bots/'+new)]
        if runtime != target and self.path(runtime).exists(): moves.append((runtime, target))
        # Snapshot complete opaque payload trees. Metadata patches are projected
        # separately; historical text is never searched/replaced.
        initial = {a: tree(self.path(a)) for a, b in moves}
        controls = {}
        operations = []
        phases = []
        roots = dict(initial)
        def snapshot():
            return {'trees': json.loads(json.dumps(roots)),
                    'controls': {p: None if b is None else digest(b) for p,b in controls.items()}}
        def control(path):
            if path not in controls: controls[path] = _raw(self.path(path))
            return controls[path]
        patches = []
        def patch(path, value, *, raw=False):
            before = control(path)
            after = value if raw else encoded(value)
            patches.append((path, before, after))
        if isolated is not None:
            patch('acceptance.json', {**isolated,'bot':new})
        info_path = 'bots/'+old+'/bot.json'
        info = json.loads(control(info_path) or b'{}')
        if not isinstance(info, dict): raise ValueError('档案显示名记录损坏')
        info['displayName'] = new; patch(info_path, info)
        if old in rows:
            updated = dict(rows); updated[new] = updated.pop(old)
            patch('core/data/'+LEDGER, {'version':1, 'bindings':updated})
            patch('bots/'+old+'/'+MARKER, {'name':new, 'token':rows[old]})
        if old == baseline: patch('core/data/bot-runtime-owner.json', {'baseline':new})
        legacy = 'core/data/active-bot.json'
        if self.path(legacy).exists():
            data = _object(self.path(legacy))
            if set(data) != {'baseline','active'}: raise ValueError('旧版选择记录需核对')
            patch(legacy, {k:new if v == old else v for k,v in data.items()})
        retired = retired_names(self.base)
        if len(retired) >= 1024: raise ValueError('保留名称记录已满')
        patch('core/data/'+RETIRED, {'version':1,'names':sorted(retired|{old})})
        from bot_rename_plugins import owner_bytes
        qq = ('core/extensions/core-infinity-qq/config.json' if old == baseline else
              'bots/'+old+'/infinity/qq/config.json')
        if self.path(qq).exists(): patch(qq, owner_bytes(control(qq), old, new), raw=True)
        # Both legacy shared QQ config and a private migrated copy can exist.
        private_qq = 'bots/'+old+'/infinity/qq/config.json'
        if private_qq != qq and self.path(private_qq).exists():
            patch(private_qq, owner_bytes(control(private_qq), old, new), raw=True)
        for delivery in ('ingress-delivery', 'local-observation-delivery'):
            journal = 'bots/'+old+'/memory/'+delivery+'/pending.json'
            if self.path(journal).exists():
                from telyincore.ingress_review import validate
                state = json.loads(control(journal))
                previous_binding = digest(str(self.path(runtime+'/events').resolve()).encode())
                validate(state, previous_binding)
                next_binding = digest(str(self.path(target+'/events').resolve()).encode())
                if previous_binding != next_binding:
                    patch(journal, {**state, 'binding':next_binding})
        from bot_offline_rename_resources import prepare
        prepare(self.core, old, new, patch)
        if runtime != target and self.path(runtime).exists():
            from telyincore.provider_path_identity import FILE, session_identity
            provider = runtime+'/provider-state'
            marker = provider+'/'+FILE
            if self.path(provider).exists():
                if self.path(marker).exists():
                    session_identity(self.path(runtime+'/session-main.jsonl'), self.path(provider))
                else:
                    patch(marker, {'version':1,'original_runtime':str(self.path(runtime).resolve())})
        # All controls are known before the first expected phase is recorded.
        phases.append(snapshot())
        for path,before,after in patches:
            operations.append({'kind':'write','path':path,'before':_pack(before),'after':_pack(after)})
            controls[path] = after
            for root, inventory in roots.items():
                if path.startswith(root+'/'):
                    rel = path[len(root)+1:]
                    inventory[str(Path(rel))] = digest(after)
            phases.append(snapshot())
        for source,destination in moves:
            if self.path(destination).exists(): raise ValueError('目标目录已存在')
            operations.append({'kind':'move','path':source,'destination':destination})
            roots[destination] = roots.pop(source)
            for path in list(controls):
                if path.startswith(source+'/'):
                    controls[destination+path[len(source):]] = controls.pop(path)
            phases.append(snapshot())
        return dict(version=1,kind=KIND,old=old,new=new,operations=operations,phases=phases)

    def _matches(self, plan, index):
        expected = plan['phases'][index]
        all_roots = {p for phase in plan['phases'] for p in phase['trees']}
        all_controls = {p for phase in plan['phases'] for p in phase['controls']}
        try:
            for root in all_roots:
                path = self.path(root)
                if root not in expected['trees']:
                    if path.exists(): return False
                elif tree(path) != expected['trees'][root]: return False
            for name in all_controls:
                raw = _raw(self.path(name))
                if (None if raw is None else digest(raw)) != expected['controls'].get(name): return False
            return True
        except (OSError, ValueError): return False

    def _effect(self, operation, reverse=False):
        if operation['kind'] == 'move':
            a,b = operation['path'],operation['destination']
            if reverse: a,b = b,a
            if self.path(b).exists(): raise ValueError('改名目标在操作期间出现，已停止')
            self.path(a).rename(self.path(b))
        else:
            raw = _unpack(operation['before' if reverse else 'after'])
            path = self.path(operation['path'])
            if raw is None: path.unlink()
            else: store(path, raw)

    def _load(self):
        gate = _object(self.base/GATE)
        if gate.get('kind') != KIND: raise ValueError('不是此功能创建的改名事务，未处理')
        ident = gate.get('transaction','')
        if len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident): raise ValueError('改名编号无效')
        folder = self.path('core/data/offline-renames/'+ident)
        p = folder/'plan.json'
        if p.stat().st_size > 32*1024*1024: raise ValueError('改名记录过大')
        raw = p.read_bytes()
        if digest(raw) != gate.get('sha256'): raise ValueError('改名计划校验失败，保留现场')
        plan = json.loads(raw)
        if plan.get('kind') != KIND or plan.get('version') != 1: raise ValueError('改名计划版本无效')
        state = _object(folder/'state.json')
        cursor = state.get('cursor')
        if type(cursor) is not int or not 0 <= cursor < len(plan['phases']): raise ValueError('改名进度无效')
        pending = state.get('pending')
        if pending is not None:
            if pending not in ('apply','rollback'): raise ValueError('改名进度无效')
            next_cursor = cursor + (1 if pending == 'apply' else -1)
            if not 0 <= next_cursor < len(plan['phases']): raise ValueError('改名进度越界')
            if self._matches(plan, next_cursor): cursor = next_cursor
            elif not self._matches(plan, cursor): raise ValueError('中断现场发生其他变化，不能自动恢复')
        elif not self._matches(plan,cursor): raise ValueError('改名现场发生其他变化，不能自动恢复')
        return folder, plan, cursor, gate

    def _drive(self, folder, plan, cursor, gate, reverse=False):
        while cursor != (0 if reverse else len(plan['operations'])):
            self.progress(f'迁移与校验 · {cursor if not reverse else len(plan["operations"])-cursor}/{len(plan["operations"])} 项已完成')
            if not self._matches(plan,cursor): raise ValueError('档案在改名期间变化，保留恢复记录')
            next_cursor = cursor + (-1 if reverse else 1)
            store(folder/'state.json', encoded({'cursor':cursor,'pending':'rollback' if reverse else 'apply'}))
            self._effect(plan['operations'][next_cursor if reverse else cursor], reverse)
            if not self._matches(plan,next_cursor): raise ValueError('改名后校验失败，保留恢复记录')
            cursor = next_cursor
            store(folder/'state.json', encoded({'cursor':cursor,'pending':None}))
        self.progress('最终核对 · 检查档案身份与目录一致性')
        self.check_layout()
        store(folder/'result.json', encoded({'state':'rolled-back' if reverse else 'committed',
                                           'old':plan['old'],'new':plan['new']}))
        if _object(self.base/GATE) != gate: raise ValueError('改名保护记录变化，未解除')
        (self.base/GATE).unlink()
        return {'ok':True,'old':plan['old'],'new':plan['new'],'name':plan['old'] if reverse else plan['new'],
                'action':'rollback' if reverse else 'rename','id':folder.name}

    def rename(self, old, new, confirm):
        if confirm != old: raise ValueError('请输入原档案名称确认')
        self.progress('检查维护锁与服务退出状态')
        with self.service.offline():
            self.progress('扫描档案与历史文件，生成改名计划')
            plan = self.plan(old,new)
            if not self._matches(plan,0): raise ValueError('改名预检期间内容变化')
            ident = uuid.uuid4().hex
            folder = self.path('core/data/offline-renames/'+ident)
            raw = encoded(plan)
            if len(raw)>32*1024*1024: raise ValueError('档案规模超过本次事务上限')
            self.progress('保存恢复记录与受控字段原值')
            store(folder/'plan.json',raw)
            store(folder/'state.json',encoded({'cursor':0,'pending':None}))
            gate={'version':1,'kind':KIND,'transaction':ident,'sha256':digest(raw)}
            store(self.base/GATE,encoded(gate))
            return self._drive(folder,plan,0,gate)

    def recover(self, *, rollback=True):
        with self.service.offline(recovery=True):
            folder,plan,cursor,gate = self._load()
            return self._drive(folder,plan,cursor,gate,reverse=rollback)
