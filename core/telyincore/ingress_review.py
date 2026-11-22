"""Local operator disposition; never replay an event or certify business success."""
import hashlib
import re
from contextlib import contextmanager
from pathlib import Path

from bot_guarded_store import GuardedStore
from bot_offline_lease import acquire_runtime_lock, release_runtime_lock, read_control
from bot_registry import bot_id
from runtime_identity import safe_path, identity_lock, bindings, MARKER
from .source_guard import lease

READER_SHA256 = '51730b32b4386ff03160fd46c1c4662e0cea4a68e49deef50368f88fc06d8fc9'


def validate(state, binding):
    if (type(state) is not dict or set(state) != {'schema','binding','generation','records'}
            or type(state['schema']) is not int or state['schema'] not in (1,2)
            or state['binding'] != binding or not _hex(state['generation'],32)
            or type(state['records']) is not dict or len(state['records']) > 128):
        raise ValueError('invalid ingress journal')
    cursors = set()
    for key,row in state['records'].items():
        if not _hex(key,32) or type(row) is not dict:
            raise ValueError('invalid ingress record')
        phase = row.get('state')
        fields = {'state'}
        original = row.get('original_state') if phase == 'RESOLVED' else phase
        phases = ('APPENDING','STORED','OFFERING') + (('QUEUED','PROCESSING') if state['schema']==2 else ())
        if original not in phases:
            raise ValueError('invalid delivery state')
        if original != 'APPENDING':
            fields |= {'cursor','digest'}
            if type(row.get('cursor')) is not int or row['cursor'] < 1 or not _hex(row.get('digest'),64):
                raise ValueError('invalid envelope binding')
            if row['cursor'] in cursors:
                raise ValueError('ambiguous delivery cursor')
            cursors.add(row['cursor'])
        if phase == 'RESOLVED':
            fields |= {'original_state','decision','evidence_sha256','operator','reviewed_revision'}
            if (original not in ('APPENDING','OFFERING','PROCESSING')
                    or row.get('decision') not in ('confirmed_handled','abandon_no_replay')
                    or not _hex(row.get('evidence_sha256'),64)
                    or not _hex(row.get('reviewed_revision'),64)
                    or not _operator(row.get('operator'))):
                raise ValueError('invalid operator disposition')
        if set(row) != fields:
            raise ValueError('invalid delivery record fields')


def _hex(value,size):
    return type(value) is str and len(value)==size and all(c in '0123456789abcdef' for c in value)


def _operator(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',value) is not None


class Review:
    def __init__(self, core, bot, *, source="plugin"):
        if source not in ("plugin", "local"):
            raise ValueError("unknown observation source")
        self.source = source
        self.directory = "ingress-delivery" if source == "plugin" else "local-observation-delivery"
        self.core=Path(core).absolute()
        self.base=self.core/'data'
        self.bots=self.core.parent/'bots'
        self.bot=bot_id(bot)

    def _paths(self):
        from bot_migration_gate import assert_identity_allowed
        safe_path(self.core.parent,self.core)
        safe_path(self.core.parent,self.bots/self.bot)
        assert_identity_allowed(self.base,self.bot)
        owner=read_control(safe_path(self.base,self.base/'bot-runtime-owner.json'))
        baseline=bot_id(owner.get('baseline'))
        token=bindings(self.base).get(self.bot)
        marker=read_control(safe_path(self.bots,self.bots/self.bot/MARKER))
        if token is None or marker != {'name':self.bot,'token':token}:
            raise ValueError('Bot identity binding mismatch')
        runtime=self.base if self.bot==baseline else self.base/'bot-runtimes'/self.bot
        safe_path(self.base,runtime)
        root=safe_path(self.bots,self.bots/self.bot/'memory'/self.directory)
        if not runtime.is_dir() or not root.is_dir():
            raise ValueError('existing Bot runtime and delivery journal required')
        binding=hashlib.sha256(str((runtime/'events').resolve()).encode()).hexdigest()
        return runtime,root,binding

    @contextmanager
    def _open(self,write=False):
        # Identity guard serializes normal launch/migration. Runtime lock blocks
        # existing cooperating cores and is retained through the entire commit.
        if not self.base.is_dir():
            raise ValueError('existing core data required')
        with identity_lock(self.base):
            before=self._paths()
            runtime,root,binding=before
            handle=None
            try:
                if write:
                    # Never write a new terminal state while the installed reader
                    # still rejects it. A later reader needs a reviewed new pin.
                    from bot_stable_file import read_stable
                    reader_path=safe_path(self.core,self.core/'telyincore/ingress_delivery.py')
                    if hashlib.sha256(read_stable(self.core,reader_path,65536)[0]).hexdigest()!=READER_SHA256:
                        raise ValueError('installed delivery reader is not the approved revision')
                    endpoint=safe_path(self.base,self.base/'instances'/(self.bot+'.json'))
                    if endpoint.exists():
                        raise RuntimeError('Bot instance record remains; do not remove it to bypass review')
                    handle=acquire_runtime_lock(runtime)
                    if handle is None:
                        raise RuntimeError('Bot is active; disposition requires cooperative retirement')
                with lease(root):
                    disk=GuardedStore(root,root/'pending.json')
                    state=disk.read()
                    validate(state,binding)
                    if self._paths()!=before:
                        raise ValueError('Bot paths changed')
                    yield disk,state
            finally:
                if handle is not None:
                    release_runtime_lock(handle)

    def show(self):
        with self._open() as (disk,state):
            return {'bot':self.bot,'source':self.source,'revision':hashlib.sha256(disk.raw).hexdigest(),
                    'generation':state['generation'],'records':state['records'],
                    'business_acknowledged':False,'replay_supported':False}

    def resolve(self, *, entry, expected_revision, decision, evidence_sha256, operator, confirm):
        if (confirm is not True or not _hex(entry,32) or not _hex(expected_revision,64)
                or decision not in ('confirmed_handled','abandon_no_replay')
                or not _hex(evidence_sha256,64) or not _operator(operator)):
            raise ValueError('explicit confirmation, exact snapshot and evidence digest required')
        with self._open(write=True) as (disk,state):
            if hashlib.sha256(disk.raw).hexdigest()!=expected_revision:
                raise ValueError('stale review snapshot; inspect again, never auto-refresh confirmation')
            row=state['records'].get(entry)
            if row is None or row['state'] not in ('APPENDING','OFFERING','PROCESSING'):
                raise ValueError('only an unresolved uncertain record may be disposed')
            row.update(original_state=row['state'],state='RESOLVED',decision=decision,
                       evidence_sha256=evidence_sha256,operator=operator,
                       reviewed_revision=expected_revision)
            validate(state,state['binding'])
            disk.write(state)  # disposition and original evidence commit together
            if disk.read()!=state:
                raise ValueError('disposition readback failed; inspect, do not retry blindly')
            return {'bot':self.bot,'entry':entry,'decision':decision,
                    'revision':hashlib.sha256(disk.raw).hexdigest(),
                    'business_acknowledged':False,'replayed':False}
