"""Explicit archival of the legacy baseline, without assigning its data to another Bot."""
from pathlib import Path
import json
from runtime_identity import safe_path, _object, MARKER, LEDGER, bindings

RECEIPT = 'bot-baseline-archive.json'
# Deployment resources stay in place. Unknown entries are never guessed private.
SHARED = frozenset({
    'bot-runtime-owner.json', 'active-bot.json', LEDGER, LEDGER+'.bak',
    'bot-retired-names.json', RECEIPT, 'bot-runtimes', 'instances',
    '.bot-identity.lock', '.host-admission.lock', '.telyincore-instance.lock',
    'offline-renames',
    'host-start-owners', 'retired-startup-owners', 'startup-port-allocation', 'retired-instance-endpoints',
    'diagnostics', 'acceptance', 'engine.json', 'engine.env', 'engine.json.lock',
    'model-proxy.lock', 'snowluma-port-allocations.json', '.history-engine.env',
})
PRIVATE = frozenset({
    'used.jsonl', 'used.jsonl.index-source.lock',  # Bot-owned retrieval usage ledger; preserve with its runtime.
    'session-main.jsonl', 'record-history', 'synthetic-history.bin', 'handoff-note.md',
    'events', 'events.index-source.lock', 'timers', 'timers.index-source.lock',
    'unresolved', 'search', 'vectors', 'provider-state', 'cache-observations-v1.json',
    'follow-up-attempt.json', 'timing.json', 'usage.json', 'wake-policy-v2.json',
    '唤醒.json', '心跳.json', 'infinity-processes', 'sharing-preview-test.json',
})


LINUX_RECEIPT = 'linux-profile-archive.json'
LINUX_PRIVATE = PRIVATE | {'session-main.jsonl.index-source.lock'}
LINUX_SHARED = SHARED | {'chatgpt-subscription', 'plugin-package-locks', 'qq-configured-account-reservations'}


def linux_profile_moves(service, name):
    from bot_offline_rename import OfflineRename
    rename = OfflineRename(service.core)
    selected = rename.isolated()
    if selected is None or selected['bot'] != name:
        raise ValueError('只可归档此部署选定的 Linux 档案')
    rename.check_layout()
    if (service.base / LINUX_RECEIPT).exists():
        raise ValueError('已有待恢复的 Linux 档案')
    moves = []
    for item in sorted(service.base.iterdir()):
        safe_path(service.base, item)
        if item.name in LINUX_PRIVATE:
            moves.append(('core/data/' + item.name, 'bot-archives/{id}/profile-runtime/' + item.name))
        elif item.name not in LINUX_SHARED:
            raise ValueError('Linux 运行目录含未分类文件，未移动：' + item.name)
    return moves


def archived_baseline(base):
    """Validate a committed archive before exempting ONLY its old missing folder."""
    base=Path(base);path=safe_path(base,base/RECEIPT)
    if not path.exists(): return None
    receipt=_object(path)
    from bot_registry import bot_id
    from bot_migration_gate import retired_names
    if set(receipt)!={'version','name','archive'} or receipt['version']!=1:
        raise ValueError('共享运行根归档凭据异常')
    name=bot_id(receipt['name']);ident=receipt['archive']
    from bot_lifecycle import BotLifecycle
    service=BotLifecycle(base.parent)
    _,row=service._journal(ident)
    if (row.get('baseline') is not True or row['name']!=name
            or row['action'] not in ('archive','delete') or row['state']!='committed'
            or row['after'].get('core/data/'+RECEIPT)!=receipt
            or name not in retired_names(base) or name in bindings(base)
            or (service.bots/name).exists()
            or _object(safe_path(base,base/'bot-runtime-owner.json'))!={'baseline':name}):
        raise ValueError('共享运行根归档凭据与当前归属不一致')
    original=json.loads(row['before']['core/data/'+LEDGER])['bindings'][name]
    marker=safe_path(service.root,service.vault/ident/'bot'/MARKER)
    if _object(marker)!={'name':name,'token':original}:
        raise ValueError('共享运行根归档原身份不匹配')
    return name


def baseline_moves(service,name):
    if (service.base/RECEIPT).exists(): raise ValueError('已有共享运行根归档凭据，未覆盖')
    if (service.base/'bot-runtimes'/name).exists(): raise ValueError('基础档案同时存在独立运行目录，需核对归属')
    if _object(service.base/'bot-runtime-owner.json')!={'baseline':name}:
        raise ValueError('共享运行根归属不一致')
    result=[]
    for path in sorted(service.base.iterdir()):
        safe_path(service.base,path)
        if path.name in PRIVATE:
            result.append(('core/data/'+path.name,'bot-archives/{id}/baseline-runtime/'+path.name))
        elif path.name not in SHARED:
            raise ValueError('共享运行根含未分类文件，未移动：'+path.name)
    config=safe_path(service.root,service.core/'extensions/core-infinity-qq/config.json')
    if config.exists():
        declared=_object(config).get('ownerBot')
        if declared not in (None,'',name): raise ValueError('共享 QQ 配置归属不一致，未移动')
        result.append(('core/extensions/core-infinity-qq/config.json','bot-archives/{id}/baseline-extension/config.json'))
    return result
