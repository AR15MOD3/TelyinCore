"""Shared reader rollback coordinator, no service actions or fence bypass.

Only an existing complete HOST registry is accepted; the default observation-only
launcher is insufficient. The caller must already own the approved fence and
target release. No production CLI, bootstrap of coverage, retry or unfence here.
"""
from pathlib import Path
import hashlib,json,os
from bot_host_registry import HostEvidenceRegistry
from bot_host_observer import HostObserver
from bot_stable_file import read_stable
from runtime_identity import safe_path
from reader_rollback_gate import compatible_reader_lease, _snapshot, REQUIRED

def sha(raw):return hashlib.sha256(raw).hexdigest()


class RegistryObserver:
    def __init__(self, registry):
        if type(registry) is not HostEvidenceRegistry:
            raise ValueError('original HOST registry required, not client JSON')
        self.registry=registry
        self.generation=None

    def __call__(self,core,names,records):
        # Called only while compatible_reader_lease holds the identity lock.
        # Lock order remains identity -> registry. The capture mutex spans probes.
        with self.registry.capture(core) as data:
            if (data['complete'] is not True or set(data['bots'])!=set(names)
                    or data['baseline'] not in names):
                raise ValueError('complete same-scope HOST coverage required')
            if self.generation is None:self.generation=data['generation']
            if self.generation!=data['generation']:
                raise ValueError('HOST generation changed')
            roster={'complete':True,'core':data['core'],'generation':data['generation'],
                    'processes':data['processes']}
            return HostObserver(roster=lambda _:roster,probe=self.registry.probe)(core,names,records)


def rollback(core,transaction,*,registry,current_pins,replacements,evidence,host_generation=None):
    """One approved bounded source transaction; leaves fence after every result.

replacements maps all three reviewed source paths to (exact bytes, target SHA).
Evidence must be a new dedicated private staging directory outside shared core.
No audit receipt grants service startup or certifies business/durable-power-loss.
"""
    core=Path(core).absolute();evidence=Path(evidence).absolute()
    safe_path(evidence.parent,evidence)
    if evidence.is_relative_to(core.parent) or evidence.exists():
        raise ValueError('fresh evidence outside installation required')
    if type(replacements) is not dict or set(replacements)!=REQUIRED:
        raise ValueError('exact complete reader/review/entry target set required')
    targets={}
    for rel,row in replacements.items():
        if (type(row) is not tuple or len(row)!=2 or type(row[0]) is not bytes
                or not 0<len(row[0])<=65536 or row[1]!=sha(row[0])):
            raise ValueError('bounded target bytes and matching digest required')
        compile(row[0],rel,'exec')  # syntax only, no execution/import
        targets[rel]=row
    observer=RegistryObserver(registry)
    observer.generation=host_generation
    with compatible_reader_lease(core,transaction,pins=current_pins,observer=observer) as permit:
        original=permit.snapshot
        bodies={rel:read_stable(core,core/rel,65536)[0] for rel in targets}
        # No evidence creation or staged source until all-Bot compatibility passes.
        evidence.mkdir()
        record={'schema':1,'transaction':transaction,'phase':'PREPARED','published':[],
                'old':{k:sha(v) for k,v in bodies.items()},
                'target':{k:v[1] for k,v in targets.items()},'fence_retained':True,
                'service_actions':0,'business_requests':0}
        def persist():
            tmp=evidence/'receipt.tmp';dest=evidence/'receipt.json'
            with tmp.open('wb') as f:
                f.write(json.dumps(record,sort_keys=True).encode());f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
        persist()
        for rel,raw in bodies.items():
            backup=evidence/'before'/rel;backup.parent.mkdir(parents=True,exist_ok=True)
            with backup.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        def action():
            try:
                for i,rel in enumerate(sorted(targets)):
                    # Continue under the same identity, runtime and journal leases.
                    effective=dict(current_pins)
                    for done in record['published']:effective[done]=targets[done][1]
                    now=_snapshot(core,permit.offline,effective)
                    untouched=set(original[1])-set(str(core/p) for p in record['published'])
                    if now[0]!=original[0] or any(now[1].get(p)!=original[1][p] for p in untouched):
                        raise ValueError('nonpublished input changed')
                    dest=safe_path(core,core/rel)
                    stage=dest.with_name(dest.name+'.rollback-'+transaction)
                    if stage.exists():raise ValueError('existing rollback stage, no retry')
                    record.update(phase='WRITING',attempting=rel);persist()
                    with stage.open('xb') as f:
                        f.write(targets[rel][0]);f.flush();os.fsync(f.fileno())
                    # Validate again AFTER staging and before replacement.
                    if _snapshot(core,permit.offline,effective)!=now:
                        raise ValueError('input changed while staging')
                    os.replace(stage,dest)
                    if read_stable(core,dest,65536)[0]!=targets[rel][0]:
                        raise ValueError('published target readback failed')
                    record['published'].append(rel);record.update(phase='READBACK',attempting=None);persist()
                final_pins={**current_pins,**{k:v[1] for k,v in targets.items()}}
                final=_snapshot(core,permit.offline,final_pins)
                changed={str(core/p) for p in targets}
                if final[0]!=original[0] or any(final[1].get(p)!=v for p,v in original[1].items() if p not in changed):
                    raise ValueError('non-source evidence changed')
                record.update(phase='COMPLETE_FENCED');persist()
                return dict(record)
            except BaseException as error:
                record.update(phase='FAILED_FENCED',error_type=type(error).__name__)
                try:persist()
                except BaseException as secondary:
                    error.add_note('rollback receipt persistence failed: '+type(secondary).__name__)
                raise
        return permit.commit(action)
