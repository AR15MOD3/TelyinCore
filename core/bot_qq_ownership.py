"""Read-only, caller-supplied QQ ownership plan. No production config discovery.

The resource witness must come from a complete trusted inventory. An empty caller
list is not evidence of production completeness. Tokens are never returned.
"""
import hashlib
import json
import re


def plan_qq_owner(raw, old, new, *, baseline, inventory):
    if any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', n)
           for n in (old, new)) or old.casefold() == new.casefold():
        raise ValueError('invalid rename identities')
    if type(baseline) is not bool or not isinstance(raw, bytes) or len(raw) > 65536:
        raise ValueError('invalid bounded config input')
    required = {'complete', 'plugins', 'process_records', 'sharing_preview', 'other_resources'}
    if (not isinstance(inventory, dict) or set(inventory) != required
            or inventory['complete'] is not True
            or inventory['plugins'] != ['core-infinity-qq']
            or inventory['process_records'] != []
            or inventory['sharing_preview'] is not False
            or inventory['other_resources'] != []):
        raise ValueError('unsupported or unresolved plugin ownership resources')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate config field')
            result[key] = value
        return result
    config = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(config, dict):
        raise ValueError('config must be an object')
    owner = config.get('ownerBot')
    # Current QQ loader uses falsy owner -> baseline/scoped Bot fallback. Only
    # documented absent/null/empty-string variants are accepted, never 0/false.
    if owner is not None and (not isinstance(owner, str) or owner not in ('', old)):
        raise ValueError('QQ ownership conflicts with source Bot')
    return {'version': 1, 'config_sha256': hashlib.sha256(raw).hexdigest(),
            'layout': 'shared-baseline' if baseline else 'bot-scoped',
            'patch': {'field': 'ownerBot', 'present': 'ownerBot' in config,
                      'before': owner, 'after': new},
            'preserve_other_fields': True, 'production_apply_supported': False,
            'requires_trusted_complete_inventory': True}


def fixture_owner_bytes(raw, plan):
    """Synthetic transaction serializer; not an approval for real config writes."""
    if hashlib.sha256(raw).hexdigest() != plan['config_sha256']:
        raise ValueError('QQ config changed since planning')
    config = json.loads(raw)
    config['ownerBot'] = plan['patch']['after']
    return json.dumps(config, ensure_ascii=False, sort_keys=True).encode('utf-8')
