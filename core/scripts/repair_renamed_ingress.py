"""Offline repair of an unchanged journal proven by a committed rename receipt."""
import base64
import json
from pathlib import Path
from bot_offline_rename import OfflineRename, digest
from bot_guarded_store import GuardedStore
from telyincore.ingress_review import validate


def repair(core, *, apply=False):
    rename=OfflineRename(core)
    results=[]
    with rename.service.offline():
        owner=json.loads((rename.base/'bot-runtime-owner.json').read_text())['baseline']
        for receipt in sorted((rename.base/'offline-renames').glob('*/result.json')):
            result=json.loads(receipt.read_text())
            if result.get('state')!='committed':continue
            plan=json.loads((receipt.parent/'plan.json').read_text())
            state=json.loads((receipt.parent/'state.json').read_text())
            if state!={'cursor':len(plan['operations']),'pending':None}:
                raise ValueError('Rename receipt is not fully committed')
            old,new=plan['old'],plan['new']
            if result!={'state':'committed','old':old,'new':new}:raise ValueError('Rename receipt identity mismatch')
            root='bots/'+new
            if not rename.path(root).exists():continue  # subsequently renamed/archived
            runtime='core/data' if new==owner or rename.isolated() else 'core/data/bot-runtimes/'+new
            previous='core/data' if new==owner or rename.isolated() else 'core/data/bot-runtimes/'+old
            binding=digest(str(rename.path(runtime+'/events').resolve()).encode())
            old_binding=digest(str(rename.path(previous+'/events').resolve()).encode())
            journal=rename.path(root+'/memory/ingress-delivery/pending.json')
            if not journal.exists():continue
            disk=GuardedStore(journal.parent,journal); data=disk.read()
            if data.get('binding')==binding:
                validate(data,binding);continue
            validate(data,old_binding)
            # Exact unchanged journal bytes must be recorded by the successful
            # transaction. A name or matching path hash alone is insufficient.
            final=plan['phases'][-1]
            rel=str(Path('memory/ingress-delivery/pending.json'))
            if final['trees'].get(root,{}).get(rel)!=digest(disk.raw):
                raise ValueError('Journal changed since rename; manual review required')
            # Check current identity against the committed control receipt.
            from runtime_identity import MARKER
            marker=root+'/'+MARKER
            expected=final['controls'].get(marker)
            if expected is None or digest(rename.path(marker).read_bytes())!=expected:
                raise ValueError('Current profile does not match rename receipt')
            from runtime_identity import bindings
            identity=json.loads(rename.path(marker).read_text())
            if bindings(rename.base).get(new)!=identity['token']:raise ValueError('Profile binding changed')
            if not rename.path(runtime+'/events').is_dir():raise ValueError('Event source is missing')
            if apply:
                from bot_offline_rename import store, encoded
                backup=receipt.parent/'ingress-binding-repair.json'
                proof={'journal_before':base64.b64encode(disk.raw).decode(),
                       'backup_before':None if disk.backup_raw is None else base64.b64encode(disk.backup_raw).decode(),
                       'old_binding':old_binding,'new_binding':binding}
                if backup.exists() and json.loads(backup.read_text())!=proof:
                    raise ValueError('Existing repair receipt differs')
                if not backup.exists():store(backup,encoded(proof))
                disk.write({**data,'binding':binding})
            results.append({'bot':new,'transaction':receipt.parent.name,'records_preserved':len(data['records']),'applied':apply})
    return results
