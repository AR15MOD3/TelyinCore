"""Offline, reversible Bot archival. Never infer process death from a missing PID.

All mutations require exclusive formal-entry admission, no unresolved startup or
instance ownership, and runtime lock probes. A durable migration fence blocks
startup after interruption; explicit rollback uses the recorded original bytes.
Legacy baseline archival separates classified private resources from shared
configuration and retains a validated inactive-root receipt.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import shutil
import uuid

from bot_registry import bot_id, BotRegistry
from runtime_identity import safe_path, identity_lock, bindings, MARKER, LEDGER, _object
from bot_migration_gate import GATE, RETIRED, assert_no_migration, retired_names
from bot_global_admission import HostAdmissionLock, validate_admission_metadata
from bot_offline_lease import acquire_runtime_lock, release_runtime_lock


def write(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='') as f:
            f.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
            f.flush(); os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def tree(path):
    """Content inventory also rejects links, special files and hardlinks."""
    safe_path(path, path)
    if path.is_file() and path.stat().st_nlink == 1:
        with path.open('rb') as stream: return {'$file': hashlib.file_digest(stream,'sha256').hexdigest()}
    if not path.is_dir(): raise ValueError('归档目录不存在')
    result = {}
    for p in sorted(path.rglob('*')):
        safe_path(path, p)
        if p.is_dir():
            result[str(p.relative_to(path)) + '/'] = None
        elif p.is_file() and p.stat().st_nlink == 1:
            with p.open('rb') as f:
                result[str(p.relative_to(path))] = hashlib.file_digest(f, 'sha256').hexdigest()
        else:
            raise ValueError('档案中有链接或特殊文件，未处理：' + str(p))
    return result


class BotLifecycle:
    def __init__(self, core):
        self.core = Path(core).absolute()
        safe_path(self.core, self.core)
        self.base = self.core / 'data'
        self.root = self.core.parent
        self.bots = self.root / 'bots'
        self.archive_relative = 'bots/.archives'

    @property
    def vault(self):
        old = safe_path(self.root, self.root / 'bot-archives')
        new = safe_path(self.root, self.root / self.archive_relative)
        if old.exists() and new.exists():
            raise ValueError('发现两处归档库，请先核对；不会覆盖或合并')
        return old if old.exists() else new

    def _relocate_vault(self):
        old = safe_path(self.root, self.root / 'bot-archives')
        new = safe_path(self.root, self.root / self.archive_relative)
        if old.exists():
            if new.exists():
                raise ValueError('发现两处归档库，未覆盖')
            # Whole-directory rename preserves old transaction bytes and hashes.
            # Only called after global/runtime offline checks under exclusive lock.
            self.bots.mkdir(parents=True, exist_ok=True)
            old.rename(new)

    def path(self, relative):
        # Version-1 journals retain their logical path; resolve through the
        # relocated vault so restore/rollback need no destructive journal rewrite.
        checked = safe_path(self.root, self.root / relative)
        parts = Path(relative).parts
        if parts and parts[0] == 'bot-archives':
            return safe_path(self.root, self.vault.joinpath(*parts[1:]))
        return checked

    def inventory(self):
        rows = bindings(self.base)
        names = set(rows)
        if self.bots.exists():
            names.update(p.name for p in self.bots.iterdir()
                         if p.is_dir() and not p.name.startswith('.'))
        archives = []
        if self.vault.exists():
            for p in sorted(self.vault.iterdir()):
                if (p / 'transaction.json').exists():
                    _, row = self._journal(p.name)
                    archives.append({k: row[k] for k in ('id', 'name', 'action', 'state')})
        return {'bots': [{'name': n, 'missing': not (self.bots / n).is_dir(),
                          'bound': n in rows} for n in sorted(names)], 'archives': archives,
                'interrupted': (self.base / GATE).exists(), 'retired': sorted(retired_names(self.base))}

    def _recover_endpoints(self, lock):
        """Retire only verified absent advertisements, retaining byte backups."""
        validate_admission_metadata(self.base, held_lock=lock)
        owners = safe_path(self.base, self.base / 'host-start-owners')
        self.offline_proof = None
        if owners.exists() and any(owners.iterdir()):
            from bot_lifecycle_boot import previous_boot_owners
            self.offline_proof = previous_boot_owners(
                [_object(safe_path(self.base, p)) for p in owners.iterdir()])
        instances = safe_path(self.base, self.base / 'instances')
        if not instances.exists(): return
        from bot_instance_recovery import recover_stale_endpoint
        for path in sorted(instances.iterdir()):
            safe_path(self.base, path)
            if path.suffix != '.json' or not path.is_file():
                raise ValueError('实例登记目录含未知文件；未清理')
            recover_stale_endpoint(self.core, bot_id(path.stem), maintenance_lock=lock)

    @contextmanager
    def offline(self, *, recovery=False):
        self.base.mkdir(exist_ok=True)
        lock = HostAdmissionLock(self.core, exclusive=True)
        lock.acquire()
        try:
            if not recovery:
                assert_no_migration(self.base)
                for name in ('host-maintenance.json', 'bot-new-deployment-gate.json'):
                    if safe_path(self.base, self.base / name).exists():
                        raise ValueError('存在其他维护门禁，未处理')
                self._recover_endpoints(lock)
            with identity_lock(self.base):
                if not recovery:
                    assert_no_migration(self.base)
                for name in ('host-maintenance.json', 'bot-new-deployment-gate.json'):
                    if safe_path(self.base, self.base / name).exists():
                        raise ValueError('存在其他维护门禁，未处理')
                validate_admission_metadata(self.base, held_lock=lock)
                instances = safe_path(self.base, self.base / 'instances')
                if instances.exists() and any(instances.iterdir()):
                    raise ValueError('仍有实例记录；请正常退出全部 Bot，不能删除记录绕过')
                owners = safe_path(self.base, self.base / 'host-start-owners')
                self.offline_proof = None
                if owners.exists() and any(owners.iterdir()):
                    from bot_lifecycle_boot import previous_boot_owners
                    rows = [_object(safe_path(self.base, p)) for p in owners.iterdir()]
                    self.offline_proof = previous_boot_owners(rows)
                roots = [self.base]
                parent = safe_path(self.base, self.base / 'bot-runtimes')
                if parent.exists():
                    for p in parent.iterdir():
                        safe_path(self.base, p)
                        if not p.is_dir(): raise ValueError('运行资料目录异常')
                        roots.append(p)
                handles = []
                try:
                    for p in roots:
                        h = acquire_runtime_lock(p)
                        if h is None: raise ValueError('Bot 尚在运行，请正常退出后再管理')
                        handles.append(h)
                    lock.check()
                finally:
                    # Windows directory moves require closing these handles.
                    # The global exclusive admission remains held through commit.
                    for h in reversed(handles): release_runtime_lock(h)
                self._relocate_vault()
                yield
                lock.check()
        finally:
            lock.close()

    def _baseline(self, name):
        for filename in ('bot-runtime-owner.json', 'active-bot.json'):
            p = self.base / filename
            if p.exists() and _object(safe_path(self.base, p)).get('baseline', '').casefold() == name.casefold():
                return True
        return False

    def _execute(self, name, action, moves, after, source=None, *, baseline=False, linux_profile=False):
        ident = uuid.uuid4().hex
        if baseline:
            from bot_baseline_archive import RECEIPT
            after = dict(after)
            after['core/data/'+RECEIPT] = (None if action == 'restore' else
                {'version':1,'name':name,'archive':ident})
        if linux_profile:
            from bot_baseline_archive import LINUX_RECEIPT
            after = dict(after)
            after['core/data/' + LINUX_RECEIPT] = None if action == 'restore' else {'name':name, 'archive':ident}
        folder = self.vault / ident
        folder.mkdir(parents=True, mode=0o700)
        before = {n: self.path(n).read_bytes().decode('utf-8') if self.path(n).exists() else None for n in after}
        planned = []
        for src, dst in moves:
            dst = dst.replace('{id}', ident)
            if self.path(dst).exists(): raise ValueError('目标已存在，未覆盖')
            planned.append({'src': src, 'dst': dst, 'hashes': tree(self.path(src))})
        row = {'version': 1, 'id': ident, 'name': name, 'action': action,
               'state': 'prepared', 'baseline': baseline, 'moves': planned, 'before': before,
               'after': after, 'source': source, 'offline_proof': self.offline_proof}
        if linux_profile:
            row['linux_profile'] = True
        journal = folder / 'transaction.json'
        if len(json.dumps(row, ensure_ascii=False).encode('utf-8')) > 15 * 1024 * 1024:
            raise ValueError('归档文件数量过多，尚未移动档案')
        write(journal, row)
        gate = {'version': 1, 'transaction': ident, 'state': 'fenced', 'kind': 'bot-lifecycle'}
        # Exclusive locks make this existence test and publication serialized.
        assert_no_migration(self.base)
        write(self.base / GATE, gate)
        try:
            for move in planned:
                src, dst = self.path(move['src']), self.path(move['dst'])
                if tree(src) != move['hashes']: raise ValueError('归档期间内容变化，已保留门禁')
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)
            for n, value in after.items():
                if value is None: self.path(n).unlink(missing_ok=True)
                else: write(self.path(n), value)
            row['state'] = 'committed'; write(journal, row)
            if _object(self.base / GATE) != gate: raise ValueError('事务门禁变化')
            (self.base / GATE).unlink()
        except BaseException:
            # No automatic deletion, retry, or broad rollback on ambiguous failure.
            raise
        return {'ok': True, 'id': ident, 'name': name, 'action': action, 'recoverable': action != 'retire-missing'}

    def remove(self, name, action, confirm, *, destination=None):
        name = bot_id(name)
        if action not in ('archive', 'delete', 'retire-missing'): raise ValueError('操作无效')
        if confirm != name: raise ValueError('请输入完整 Bot 文件夹名确认')
        if destination is not None and action != 'archive':
            raise ValueError('迁出必须保留本机归档')
        with self.offline():
            baseline = self._baseline(name)
            linux_profile = self.root.name == '.linux-acceptance'
            if linux_profile and action not in ('archive', 'delete'):
                raise ValueError('Linux 单档案需要完整目录才能归档')
            if baseline and action == 'retire-missing': raise ValueError('基础档案缺失时请先恢复原档案；不能伪造完整归档')
            rows = bindings(self.base)
            if rows and not (self.base / 'bot-runtime-owner.json').exists():
                raise ValueError('已有运行登记但缺少归属记录；请先核对来源')
            retired = retired_names(self.base)
            if len(retired) >= 1024: raise ValueError('退役登记已达到容量上限')
            if name.casefold() in {n.casefold() for n in retired}: raise ValueError('该身份已退役')
            folder = self.path('bots/' + name)
            runtime = self.path('core/data/bot-runtimes/' + name)
            moves = []
            if action == 'retire-missing':
                if folder.exists() or name not in rows: raise ValueError('仅用于已登记且原目录缺失的身份')
                for other in self.bots.iterdir():
                    if other.name == '.deleted' or other.name.startswith('.bot-init-'): continue
                    marker = safe_path(self.bots, other / MARKER)
                    if marker.is_file() and _object(marker).get('token') == rows[name]:
                        raise ValueError('其他目录仍带有此身份，可能只是改名；请恢复原目录名')
            else:
                if not BotRegistry(self.bots, Path(), '').valid(folder): raise ValueError('Bot 档案不存在或不完整')
                if name in rows and _object(safe_path(self.bots, folder / MARKER)) != {'name': name, 'token': rows[name]}:
                    raise ValueError('Bot 身份不匹配')
                if name not in rows and (folder / MARKER).exists():
                    from runtime_identity import profile_issues
                    if issue := profile_issues(self.base, self.bots).get(name):
                        raise ValueError(issue)
                moves.append(('bots/' + name, 'bot-archives/{id}/bot'))
                if linux_profile:
                    from bot_baseline_archive import linux_profile_moves
                    moves.extend(linux_profile_moves(self, name))
                elif baseline:
                    from bot_baseline_archive import baseline_moves
                    if name not in rows: raise ValueError('基础档案缺少身份绑定，未移动')
                    moves.extend(baseline_moves(self,name))
            if runtime.exists():
                if name not in rows: raise ValueError('运行资料没有匹配的身份登记')
                moves.append(('core/data/bot-runtimes/' + name, 'bot-archives/{id}/runtime'))
            export = self._export_profile(name, destination, moves) if destination is not None else None
            new = dict(rows); new.pop(name, None)
            ledger = {'version': 1, 'bindings': new} if (self.base / LEDGER).exists() else None
            try:
                result = self._execute(name, action, moves, {
                    'core/data/' + LEDGER: ledger,
                    'core/data/' + RETIRED: {'version': 1, 'names': sorted(retired | {name})}},
                    baseline=baseline, linux_profile=linux_profile)
            except (OSError, ValueError) as error:
                if export is None:
                    raise
                raise ValueError(f'迁出副本已保存到 {export}，本机归档未确认完成：{error}') from error
            if export is not None:
                result['destination'] = str(export)
            return result

    def register_copy(self, name, confirm):
        """Give a verified local copy its own portable marker before first wake."""
        from runtime_identity import _publish, profile_issues, reserved_names

        name = bot_id(name)
        if confirm != name:
            raise ValueError('请输入副本的完整文件夹名确认')
        with self.offline():
            if name.casefold() in {n.casefold() for n in reserved_names(self.base) | retired_names(self.base)}:
                raise ValueError('这个名称已有本机登记，请换一个新名称')
            folder = self.path('bots/' + name)
            if not BotRegistry(self.bots, Path(), '').valid(folder):
                raise ValueError('副本的人设文件不完整或无法读取')
            if [p.name for p in self.bots.iterdir() if p.name.casefold() == name.casefold()] != [name]:
                raise ValueError('副本的文件夹名称存在冲突')
            marker = safe_path(self.bots, folder / MARKER)
            copied = _object(marker)
            original = bot_id(copied.get('name'))
            rows = bindings(self.base)
            if (original == name or original not in rows
                    or copied != {'name': original, 'token': rows[original]}
                    or original in profile_issues(self.base, self.bots)):
                raise ValueError('找不到与副本对应的正常原档案；请先核对来源')
            _publish(marker, {'name': name, 'token': uuid.uuid4().hex})
        return {'name': name}

    def _export_profile(self, name, destination, moves):
        parent = Path(destination).expanduser().absolute()
        safe_path(Path(parent.anchor), parent)
        parent = parent.resolve(strict=True)
        if not parent.is_dir() or parent.is_relative_to(self.root):
            raise ValueError('请选择本安装目录以外的现有文件夹')
        target = safe_path(parent, parent / name)
        if any(entry.name.casefold() == name.casefold() for entry in parent.iterdir()):
            raise ValueError('目标文件夹已有同名档案；原档案仍在')
        # Reserve the final name without replacing an existing folder. Partial
        # copies stay hidden and the source is untouched if copying fails.
        stage = parent / ('.bot-export-' + uuid.uuid4().hex)
        stage.mkdir(mode=0o700)
        try:
            folder = self.path('bots/' + name)
            expected = tree(folder)
            shutil.copytree(folder, stage, dirs_exist_ok=True)
            if tree(stage) != expected or tree(folder) != expected:
                raise ValueError('档案复制后的内容校验失败，原档案仍在')
            backup = stage / '.previous-installations' / uuid.uuid4().hex
            for source, archive in moves:
                if source == 'bots/' + name:
                    continue
                original = self.path(source)
                expected = tree(original)
                relative = Path(archive).parts[2:]
                copy = backup.joinpath(*relative)
                copy.parent.mkdir(parents=True, exist_ok=True)
                if original.is_dir():
                    shutil.copytree(original, copy)
                else:
                    shutil.copy2(original, copy)
                if tree(copy) != expected or tree(original) != expected:
                    raise ValueError('运行资料复制后的内容校验失败，原档案仍在')
            write(stage / '.runtime-export.json', {
                'version': 1, 'backup': backup.name if backup.exists() else None})
            # Exclusive reservation also works on filesystems where rename
            # could otherwise replace a concurrent empty directory.
            target.mkdir(mode=0o700)
            try:
                # Move within the export volume. Publish persona last so a
                # concurrently scanning launcher cannot select a partial copy.
                for entry in sorted(stage.iterdir(), key=lambda path: path.name == 'persona'):
                    entry.rename(target / entry.name)
            except BaseException:
                shutil.rmtree(target)
                raise
            return target
        finally:
            shutil.rmtree(stage)

    def _journal(self, ident):
        if len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident): raise ValueError('归档编号无效')
        p = self.path('bot-archives/' + ident + '/transaction.json')
        if p.stat().st_size > 16 * 1024 * 1024: raise ValueError('事务记录过大')
        row = json.loads(p.read_text('utf-8'))
        if row.get('version') != 1 or row.get('id') != ident: raise ValueError('归档记录不匹配')
        name = bot_id(row['name'])
        keys = {'core/data/' + LEDGER, 'core/data/' + RETIRED}
        if row.get('baseline') is True:
            from bot_baseline_archive import RECEIPT
            keys.add('core/data/'+RECEIPT)
        if row.get('linux_profile') is True:
            from bot_baseline_archive import LINUX_RECEIPT
            if self.root.name != '.linux-acceptance' or row.get('baseline'):
                raise ValueError('Linux 档案记录归属不匹配')
            keys.add('core/data/' + LINUX_RECEIPT)
        if set(row['before']) != keys or set(row['after']) != keys:
            raise ValueError('事务登记范围异常')
        action = row['action']
        origin = row.get('source') if action == 'restore' else ident
        if not isinstance(origin, str) or len(origin) != 32 or any(c not in '0123456789abcdef' for c in origin):
            raise ValueError('事务来源异常')
        allowed = {('bots/' + name, 'bot-archives/' + origin + '/bot'),
                   ('core/data/bot-runtimes/' + name, 'bot-archives/' + origin + '/runtime')}
        if row.get('baseline') is True:
            from bot_baseline_archive import PRIVATE
            allowed |= {('core/data/'+n,'bot-archives/'+origin+'/baseline-runtime/'+n) for n in PRIVATE}
            allowed.add(('core/extensions/core-infinity-qq/config.json','bot-archives/'+origin+'/baseline-extension/config.json'))
        if row.get('linux_profile') is True:
            from bot_baseline_archive import LINUX_PRIVATE
            allowed |= {('core/data/'+n, 'bot-archives/'+origin+'/profile-runtime/'+n) for n in LINUX_PRIVATE}
        pairs = [(m['src'], m['dst']) for m in row['moves']]
        if action == 'restore': allowed = {(b, a) for a, b in allowed}
        if (action not in ('archive', 'delete', 'retire-missing', 'restore')
                or len(pairs) != len(set(pairs)) or not set(pairs) <= allowed):
            raise ValueError('事务目录范围异常')
        return p, row

    def restore(self, ident, confirm):
        with self.offline():
            _, row = self._journal(ident)
            name = bot_id(row['name'])
            if confirm != name: raise ValueError('请输入完整 Bot 文件夹名确认恢复')
            if row['state'] != 'committed' or row['action'] not in ('archive', 'delete'):
                raise ValueError('此记录没有完整 Bot 档案可恢复；误删残余不能冒充完整档案')
            rows = bindings(self.base); retired = retired_names(self.base)
            if row.get('baseline') is True:
                from bot_baseline_archive import archived_baseline, RECEIPT
                if archived_baseline(self.base) != name or _object(self.base/RECEIPT)['archive'] != ident:
                    raise ValueError('基础档案归档凭据已变化，未恢复')
            if row.get('linux_profile') is True:
                from bot_baseline_archive import LINUX_RECEIPT
                from bot_offline_rename import OfflineRename
                selected = OfflineRename(self.core).isolated()
                if (not selected or selected['bot'] != name or
                        _object(self.path('core/data/' + LINUX_RECEIPT)) != {'name':name, 'archive':ident}):
                    raise ValueError('当前 Linux 档案与归档记录不匹配')
            if name not in retired or name in rows: raise ValueError('身份状态已变化，不能重复恢复')
            moves = []
            for move in row['moves']:
                if tree(self.path(move['dst'])) != move['hashes']: raise ValueError('归档内容已变化，未恢复')
                moves.append((move['dst'], move['src']))
            old = json.loads(row['before']['core/data/' + LEDGER]) if row['before']['core/data/' + LEDGER] else {'bindings': {}}
            if name in old['bindings']: rows[name] = old['bindings'][name]
            ledger = {'version': 1, 'bindings': rows} if (self.base / LEDGER).exists() else None
            return self._execute(name, 'restore', moves, {
                'core/data/' + LEDGER: ledger,
                'core/data/' + RETIRED: {'version': 1, 'names': sorted(retired - {name})}}, source=ident, baseline=row.get('baseline') is True,
                    linux_profile=row.get('linux_profile') is True)

    def rollback(self):
        with self.offline(recovery=True):
            gate = _object(self.base / GATE)
            if gate.get('kind') != 'bot-lifecycle': raise ValueError('不是 Bot 归档事务，不能处理其他门禁')
            journal, row = self._journal(gate['transaction'])
            # Preflight everything before touching any byte; a partial rollback is
            # itself repeatable as each path must be either original or moved.
            for move in row['moves']:
                src, dst = self.path(move['src']), self.path(move['dst'])
                if src.exists() == dst.exists(): raise ValueError('事务目录冲突，保留现场')
                if tree(src if src.exists() else dst) != move['hashes']: raise ValueError('事务内容变化，保留现场')
            for n, before in row['before'].items():
                p = self.path(n)
                current = p.read_bytes().decode('utf-8') if p.exists() else None
                after = row['after'][n]
                matches_after = (current is None if after is None else current is not None and json.loads(current) == after)
                if current != before and not matches_after:
                    raise ValueError('身份登记在事务外变化，保留现场')
            for move in reversed(row['moves']):
                src, dst = self.path(move['src']), self.path(move['dst'])
                if dst.exists():
                    src.parent.mkdir(parents=True, exist_ok=True); dst.rename(src)
            for n, before in row['before'].items():
                if before is None: self.path(n).unlink(missing_ok=True)
                else: write(self.path(n), before)
            row['state'] = 'rolled-back'; write(journal, row)
            (self.base / GATE).unlink()
            return {'ok': True, 'name': row['name'], 'action': 'rollback'}
