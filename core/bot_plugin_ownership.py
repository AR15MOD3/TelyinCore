"""Read-only plugin/account inventory boundary. No filesystem/network discovery.

Only a trusted host-owned inventory callable can attest complete deployment scope.
This prototype has NO production provider. JSON supplied by a client is not an
authoritative inventory, even when its complete field is true.
"""
import copy
import hashlib
from pathlib import Path, PurePosixPath
import re

from bot_registry import bot_id
from bot_qq_ownership import plan_qq_owner


def _account(value):
    # Account equality, not format guessing from URLs, ports, tokens or file names.
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,20}', value):
        raise ValueError('QQ account identity unresolved')
    value = str(int(value))
    if value == '0':
        raise ValueError('QQ account identity unresolved')
    return value


class PluginOwnershipObserver:
    """Pinned inventory for one review; instantiate anew after an approved change.

No receipt grants write permission, process absence or production migration.
All account identifiers stay internal; report only categorical blocking reasons.
"""
    def __init__(self, *, inventory):
        if not callable(inventory):
            raise ValueError('trusted inventory callable required')
        self._inventory = inventory
        self._pinned = None

    def _observe(self, core, bots):
        try:
            names = list(bots)
            if not names or len(names) > 1024:
                raise ValueError()
            for name in names:
                bot_id(name)
            if len({n.casefold() for n in names}) != len(names):
                raise ValueError()
            data = copy.deepcopy(self._inventory(Path(core).resolve()))
            if (not isinstance(data, dict) or set(data) != {
                    'version', 'core', 'complete', 'generation', 'bots', 'baseline', 'resources'}
                    or type(data['version']) is not int or data['version'] != 1
                    or data['core'] != str(Path(core).resolve()) or data['complete'] is not True
                    or not isinstance(data['generation'], str) or not 1 <= len(data['generation']) <= 64
                    or not isinstance(data['bots'], list) or sorted(data['bots']) != sorted(names)
                    or data['baseline'] not in names or not isinstance(data['resources'], list)
                    or len(data['resources']) > 4096):
                raise ValueError()
            paths = set()
            for row in data['resources']:
                if (not isinstance(row, dict) or set(row) != {
                        'path', 'plugin', 'kind', 'owner', 'account', 'sha256'}
                        or not isinstance(row['path'], str) or len(row['path']) > 1024
                        or any(c in row['path'] for c in ('\\', ':', '\x00'))
                        or PurePosixPath(row['path']).is_absolute()
                        or '..' in PurePosixPath(row['path']).parts
                        or str(PurePosixPath(row['path'])) != row['path']
                        or row['path'].casefold() in paths
                        or not isinstance(row['plugin'], str) or not isinstance(row['kind'], str)
                        or row['owner'] not in names
                        or not isinstance(row['sha256'], str)
                        or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
                    raise ValueError()
                paths.add(row['path'].casefold())
            # Pin full exact contents, not generation alone. Never export this
            # snapshot: it can contain private account identifiers.
            if self._pinned is None:
                self._pinned = copy.deepcopy(data)
            elif self._pinned != data:
                raise ValueError()
            return data
        except Exception:
            raise ValueError('plugin inventory incomplete, invalid or changed') from None

    @staticmethod
    def _review(data, old, new):
        names = data['bots']
        if old not in names or any(n.casefold() == new.casefold() for n in names):
            raise ValueError('rename source/destination inventory conflict')
        bot_id(new)
        blockers = set()
        accounts = set()
        configs = []
        for row in data['resources']:
            if row['plugin'] != 'core-infinity-qq':
                blockers.add('unknown_plugin_requires_adapter')
                continue
            if row['kind'] != 'qq-config':
                blockers.add({'process-record': 'process_record_requires_offline_adapter',
                              'sharing-preview': 'sharing_preview_authorization_not_migratable',
                              'voice-corpus': 'voice_corpus_requires_adapter'}.get(
                                  row['kind'], 'unknown_plugin_resource_requires_adapter'))
                continue
            expected = ('core/extensions/core-infinity-qq/config.json' if row['owner'] == data['baseline']
                        else 'bots/' + row['owner'] + '/infinity/qq/config.json')
            if row['path'] != expected:
                blockers.add('config_path_owner_layout_conflict')
            try:
                account = _account(row['account'])
                if account in accounts:
                    blockers.add('duplicate_qq_account_claim')
                accounts.add(account)
            except ValueError:
                blockers.add('qq_account_identity_unresolved')
            configs.append(row)
        source = [row for row in configs if row['owner'] == old]
        if len(source) != 1:
            blockers.add('source_qq_config_missing_or_ambiguous')
        return sorted(blockers), source

    def review(self, core, bots, old, new):
        data = self._observe(core, bots)
        blockers, source = self._review(data, old, new)
        return {'version': 1, 'resource_count': len(data['resources']),
                'resources': [{key: row[key] for key in ('path', 'plugin', 'kind', 'owner')}
                              for row in data['resources']],
                'qq_config_count': sum(r['kind'] == 'qq-config' and r['plugin'] == 'core-infinity-qq'
                                       for r in data['resources']),
                'blockers': blockers, 'fixture_eligible': not blockers,
                'production_apply_supported': False, 'requires_trusted_complete_inventory': True}

    def plan_qq(self, raw, old, new, *, baseline, core, bots, config_relative):
        data = self._observe(core, bots)
        blockers, source = self._review(data, old, new)
        if blockers:
            raise ValueError('plugin ownership review blocked: ' + ','.join(blockers))
        row = source[0]
        if (row['path'] != config_relative or type(baseline) is not bool
                or baseline != (old == data['baseline']) or not isinstance(raw, bytes)
                or len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != row['sha256']):
            raise ValueError('QQ config does not match pinned inventory')
        plan = plan_qq_owner(raw, old, new, baseline=baseline, inventory={
            'complete': True, 'plugins': ['core-infinity-qq'], 'process_records': [],
            'sharing_preview': False, 'other_resources': []})
        # Revalidate trusted snapshot after planning. Coordinator must also repeat
        # under its offline lock; this check cannot seal a real filesystem race.
        self._observe(core, bots)
        return dict(plan, cross_bot_account_check=True, plugin_inventory_checked=True)
