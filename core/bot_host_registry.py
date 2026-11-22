"""Opt-in host registry and verified read-only account receipt producer.

UNSEALED by default. No startup hooks, adoption, process launch/kill or automatic
requests. A trusted exclusive-host coverage authority must certify empty bootstrap;
client JSON/callback labels cannot prove coverage of unmanaged/legacy start paths.
Lock order: global identity -> registry mutex. Mutators must be called OUTSIDE an
already-held identity lock. Capture takes only the bounded, nonblocking mutex.
"""
import copy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from threading import Event, Lock
import time
from urllib.parse import urlsplit
import uuid

import httpx

from bot_host_inventory import _raw, _identity
from bot_host_observer import probe_process
from bot_joint_offline import _control
from bot_migration_gate import assert_no_migration
from bot_plugin_ownership import _account
from bot_registry import bot_id
from qq_protocol_health import eligible
from runtime_identity import identity_lock, safe_path
from snowluma_connection import management_listener_owners


def _sha(raw): return hashlib.sha256(raw).hexdigest()


def _now(clock):
    value=clock()
    if type(value) is not int or value<=0: raise ValueError('invalid receipt clock')
    return value


def read_login_info(config, target, *, current, trusted, clock=time.monotonic_ns, client=httpx.Client):
    """One bounded POST /get_login_info; no redirect/proxy/login/message/retry."""
    try:
        if not eligible(config,target) or not current() or not trusted(): raise ValueError()
        start=_now(clock)
        with client(timeout=2,trust_env=False,follow_redirects=False,
                    headers={'Authorization':'Bearer '+config['token']}) as session:
            with session.stream('POST',config['httpUrl'].rstrip('/')+'/get_login_info',json={}) as response:
                if response.status_code!=200: raise ValueError()
                raw=bytearray()
                for chunk in response.iter_bytes():
                    if (len(raw)+len(chunk)>65536 or not current()
                            or not 0<=_now(clock)-start<=6_000_000_000): raise ValueError()
                    raw.extend(chunk)
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result: raise ValueError()
                result[key]=value
            return result
        body=json.loads(raw,object_pairs_hook=unique)
        if (not isinstance(body,dict) or body.get('status')!='ok' or type(body.get('retcode')) is not int
                or body['retcode']!=0 or not isinstance(body.get('data'),dict)): raise ValueError()
        observed=_account(body['data'].get('user_id'))
        if (observed!=target or not current() or not trusted()
                or not 0<=_now(clock)-start<=6_000_000_000): raise ValueError()
        return observed,start
    except Exception:
        raise ValueError('verified read-only login evidence unavailable') from None


class _StartWindow:
    def __init__(self,registry,bot,floor):
        self.registry,self.bot,self.floor=registry,bot,floor
        self.active=True;self.committed=False

    def commit(self,pid,*,untracked_descendants=False):
        if not self.active or self.committed: raise ValueError('start window expired or already committed')
        if self.registry._fault.is_set(): raise ValueError('host coverage revoked')
        if type(untracked_descendants) is not bool: raise ValueError('invalid descendant coverage marker')
        if type(pid) is not int or not 0<pid<0xFFFFFFFF: raise ValueError('invalid new PID')
        row=self.registry._alive(pid)
        if row['created_filetime']<self.floor or pid in self.registry._processes:
            raise ValueError('existing/reused process cannot be adopted')
        self.registry._processes[pid]=dict(row,bot=self.bot)
        # Revoke atomically while the birth lock is still held. This flag can
        # only remove coverage, never certify a child's unmanaged launch paths.
        if untracked_descendants: self.registry._tainted=True
        self.registry._advance();self.committed=True


class _ObservedWindow:
    """Own Popen birth observation only; NEVER a sealed/complete start grant."""
    def __init__(self,registry,bot,floor):
        self.registry,self.bot,self.floor=registry,bot,floor
        self.active=True;self.committed=False

    def commit(self,pid,*,untracked_descendants=True):
        if (not self.active or self.committed or untracked_descendants is not True
                or self.registry._sealed or not self.registry._fault.is_set()
                or type(pid) is not int or not 0<pid<0xFFFFFFFF):
            raise ValueError('invalid single-use observed birth')
        row=self.registry._alive(pid)
        if row['created_filetime']<self.floor or self.registry._processes:
            raise ValueError('observed birth cannot adopt existing/reused process')
        self.registry._processes[pid]=dict(row,bot=self.bot)
        self.registry._tainted=True
        self.registry._advance();self.committed=True


class HostEvidenceRegistry:
    def __init__(self,core,bots,baseline,*,probe=probe_process,listeners=management_listener_owners,
                 clock_ns=time.monotonic_ns,birth_clock=lambda:time.time_ns()//100+116444736000000000, revocations=None):
        self.core=Path(core).absolute();safe_path(self.core.parent,self.core)
        names=sorted(bot_id(name) for name in bots)
        if (not names or len(names)>1024 or len({name.casefold() for name in names})!=len(names)
                or baseline not in names or not all(callable(fn) for fn in (probe,listeners,clock_ns,birth_clock))):
            raise ValueError('invalid host registry scope/providers')
        self.bots,self.baseline=names,baseline
        self.probe,self.listeners,self.clock,self.birth_clock=probe,listeners,clock_ns,birth_clock
        self._lock=Lock();self._sealed=False;self._tainted=False
        self._coverage_check=None
        self._fault=Event()  # irreversible in this registry; safe to trip while read-locked
        self._processes={};self._bindings={};self._proofs={};self._advance()
        self._account_changes=set()  # sticky until a NEW trusted registry/bootstrap
        if revocations is not None:
            from bot_revocation_journal import RevocationJournal
            if type(revocations) is not RevocationJournal or revocations.core.resolve() != self.core.resolve():
                raise ValueError('explicit same-core revocation journal required')
        self._revocations=revocations
        self._sync_revocations()

    def _revocation_scopes(self):
        return {('core/extensions/core-infinity-qq/config.json' if bot == self.baseline
                 else 'bots/'+bot+'/infinity/qq/config.json') for bot in self.bots}

    def _sync_revocations(self):
        if self._revocations is None: return
        try:
            loaded=self._revocations.load(self._revocation_scopes())
        except BaseException:
            self.revoke_coverage()
            raise
        added=loaded-self._account_changes
        self._account_changes.update(loaded)
        for path in loaded: self._proofs.pop(path,None)
        if added: self._advance()

    def _persist_account_denial(self,relative):
        self._proofs.pop(relative,None)
        self._account_changes.add(relative);self._advance()
        if self._revocations is not None:
            try: self._revocations.record(relative,self._revocation_scopes())
            except BaseException:
                self.revoke_coverage()
                raise


    def _advance(self): self._generation=uuid.uuid4().hex

    @contextmanager
    def _mutex(self):
        if not self._lock.acquire(blocking=False): raise ValueError('host registry busy')
        try: yield
        finally: self._lock.release()

    @contextmanager
    def _write(self,*,allow_fenced=False):
        if not (self.core/'data').is_dir(): raise ValueError('host deployment missing')
        with identity_lock(self.core/'data'):
            with self._mutex():
                if not allow_fenced: assert_no_migration(self.core/'data')
                yield

    def bootstrap_empty(self,coverage):
        """Trusted exclusive-host bootstrap only; never import preexisting PIDs.

The external authority must hold unmanaged/legacy launches out and attest that
ALL future starts pass through the guarded windows. No authority is bundled.
"""
        with self._write():
            self._sync_revocations()
            self._sealed=False;self._advance();self._proofs.clear()
            if self._fault.is_set(): raise ValueError('host channel fault requires fresh trusted bootstrap')
            if self._account_changes: raise ValueError('account mutation requires fresh trusted bootstrap')
            if self._processes: raise ValueError('registered processes must retire, not be forgotten')
            try:
                if not callable(coverage): raise ValueError()
                attestation=coverage(self.core.resolve())
                expected={'core':str(self.core.resolve()),'bots':self.bots,'baseline':self.baseline,
                          'complete':True,'processes':[],'entrypoints_guarded':True}
                if json.dumps(attestation,sort_keys=True)!=json.dumps(expected,sort_keys=True): raise ValueError()
            except Exception:
                raise ValueError('exclusive empty-host coverage authority required') from None
            check=getattr(coverage,'check',None)
            self._coverage_check=check if callable(check) else None
            self._sealed=True;self._tainted=False;self._advance()

    @contextmanager
    def start_window(self,bot):
        """Trusted launcher creates its own child INSIDE this held window.

This API does not spawn anything. Uncommitted/failed windows revoke coverage;
there is no blind cancel/forget operation or adoption of an old process.
"""
        with self._write():
            self._sync_revocations()
            if bot not in self.bots or not self._sealed or self._tainted or self._fault.is_set() or self._account_changes:
                raise ValueError('host start coverage unavailable')
            floor=self.birth_clock()
            if type(floor) is not int or floor<=0: raise ValueError('invalid birth clock')
            window=_StartWindow(self,bot,floor)
            self._advance()
            try:
                yield window
                if self._fault.is_set(): raise ValueError('host coverage revoked during birth')
                if not window.committed: raise ValueError('start window not committed')
            except BaseException:
                self._tainted=True;self._advance()
                raise
            finally: window.active=False

    @contextmanager
    def observe_window(self,bot):
        """Fresh registry: own immediate child only, permanently incomplete.

        No coverage callback/complete=true. The fault latch prevents this object
        from later being sealed, even after retirement. Caller must spawn here.
        """
        with self._write():
            if (bot not in self.bots or self._sealed or self._fault.is_set()
                    or self._processes or self._bindings or self._proofs
                    or self._account_changes or self._revocations is not None):
                raise ValueError('fresh observation-only registry required')
            self.revoke_coverage();self._tainted=True;self._advance()
            floor=self.birth_clock()
            if type(floor) is not int or floor<=0:raise ValueError('invalid observed birth clock')
            window=_ObservedWindow(self,bot,floor)
            try:
                yield window
                if not window.committed:raise ValueError('observed birth was not committed')
            finally:window.active=False

    def _alive(self,pid):
        try: seen=self.probe(pid)
        except Exception:
            raise ValueError('owned native process identity unavailable') from None
        if (not isinstance(seen,dict) or seen.get('state')!='alive'
                or type(seen.get('pid')) is not int or seen['pid']!=pid):
            raise ValueError('owned native process identity unavailable')
        return _identity({key:seen.get(key) for key in ('pid','created_filetime','image')})

    def retire(self,pid):
        """Record verified exit only. Does not stop a process/delete PID files."""
        with self._write(allow_fenced=True):
            if type(pid) is not int or pid not in self._processes: raise ValueError('unowned process')
            try: seen=self.probe(pid)
            except Exception:
                raise ValueError('owned process absence unavailable') from None
            if (not isinstance(seen,dict) or type(seen.get('pid')) is not int or seen['pid']!=pid
                    or seen.get('state')!='absent'): raise ValueError('owned process not independently absent')
            del self._processes[pid]
            for proof in self._proofs.values():
                if proof['endpoint']['pid']==pid: proof['endpoint_state']='retired'
            self._advance()  # original query timestamp is deliberately NOT refreshed

    def bind_config(self,bot,relative,target_account):
        """Publish existing config revision/target, never write config or log in."""
        with self._write():
            self._sync_revocations()
            if relative in self._account_changes:
                raise ValueError('account change is pending; cannot rebind this registry')
            if bot not in self.bots: raise ValueError('unknown host Bot')
            expected=('core/extensions/core-infinity-qq/config.json' if bot==self.baseline
                      else 'bots/'+bot+'/infinity/qq/config.json')
            if relative!=expected: raise ValueError('host binding path mismatch')
            self._proofs.pop(relative,None);self._bindings.pop(relative,None);self._advance()
            target=_account(target_account)
            raw=_raw(self.core.parent,self.core.parent/relative)
            config=_control(self.core.parent,self.core.parent/relative)
            owner=config.get('ownerBot')
            if owner is None or owner=='': owner=bot
            if owner!=bot or not eligible(config,target): raise ValueError('host config owner/endpoint invalid')
            if _sha(_raw(self.core.parent,self.core.parent/relative))!=_sha(raw): raise ValueError('config changed')
            self._bindings[relative]={'bot':bot,'target':target,'sha256':_sha(raw),'nonce':uuid.uuid4().hex}

    def invalidate_account(self,relative):
        """Drop one receipt only; actual writers must use prepare_account_change."""
        with self._write(allow_fenced=True):
            if relative not in self._bindings: raise ValueError('unknown account binding')
            self._proofs.pop(relative,None);self._advance()

    def prepare_account_change(self,relative):
        """Denial-only barrier BEFORE a cooperative config/account writer.

Unlike one-shot receipt invalidation, no rebind/refresh/bootstrap on this object
can clear this marker. There is deliberately no client 'commit/end/unlock' RPC.
"""
        with self._write():
            self._sync_revocations()
            if relative not in self._bindings: raise ValueError('unknown account binding')
            self._persist_account_denial(relative)

    def prepare_recovery_change(self,bot):
        """Denial-only account scope barrier, even before a config is bound.

No credential/path supplied by a worker, no file read, no end/reset operation.
"""
        with self._write():
            self._sync_revocations()
            if bot not in self.bots: raise ValueError('unknown recovery Bot scope')
            relative = ('core/extensions/core-infinity-qq/config.json' if bot == self.baseline
                        else 'bots/'+bot+'/infinity/qq/config.json')
            self._persist_account_denial(relative)

    def refresh_account(self,relative,pid,*,client=httpx.Client):
        """Explicitly request one read-only proof from an already-owned endpoint."""
        with self._write():
            self._sync_revocations()
            self._proofs.pop(relative,None);self._advance() # revoke even on query failure
            try:
                if relative in self._account_changes: raise ValueError()
                if type(pid) is not int or not 0<pid<0xFFFFFFFF: raise ValueError()
                binding=copy.deepcopy(self._bindings[relative]);registered=self._processes[pid]
                if registered['bot']!=binding['bot']: raise ValueError()
                identity=_identity({k:registered[k] for k in ('pid','created_filetime','image')})
                path=self.core.parent/relative;raw=_raw(self.core.parent,path)
                config=_control(self.core.parent,path)
                def current():
                    return (self._bindings.get(relative)==binding and self._processes.get(pid)==registered
                            and _sha(_raw(self.core.parent,path))==binding['sha256'])
                def trusted():
                    if self._alive(pid)!=identity: return False
                    for field in ('httpUrl','wsUrl'):
                        port=urlsplit(config[field]).port
                        if self.listeners(port)!=[{'address':'127.0.0.1','pid':pid}]: return False
                    return self._alive(pid)==identity
                if _sha(raw)!=binding['sha256']: raise ValueError()
                observed,at=read_login_info(config,binding['target'],current=current,trusted=trusted,
                                           clock=self.clock,client=client)
                self._proofs[relative]={'path':relative,'config_sha256':binding['sha256'],'bot':binding['bot'],
                    'target_account':binding['target'],'observed_account':observed,'source':'get_login_info',
                    'sampled_at_ns':at,'endpoint_verified':True,'endpoint_state':'active','endpoint':identity,
                    '_binding_nonce':binding['nonce']}
                self._advance()
            except Exception:
                raise ValueError('host account proof revoked or unavailable') from None

    def revoke_coverage(self):
        """Sticky denial-only latch: no IO/locks, no import/reset/forget authority.

A channel fault must revoke even while capture/global locks are held. An in-flight
capture raises on context exit; restarting requires a NEW registry plus genuine
exclusive empty-host authority, not re-sealing this faulted object.
"""
        self._fault.set()

    @contextmanager
    def capture(self,core):
        """ReadonlyHostInventory capture source; contains PRIVATE account fields."""
        if Path(core).resolve()!=self.core.resolve(): raise ValueError('host registry scope mismatch')
        with self._mutex():
            if self._coverage_check is not None:
                try:self._coverage_check()
                except BaseException:
                    self.revoke_coverage()
                    raise
            self._sync_revocations()
            fault_at_entry=self._fault.is_set()
            denials_at_entry=set(self._account_changes)
            now=_now(self.clock);complete=(self._sealed and not self._tainted
                and not fault_at_entry and not self._account_changes);accounts=[]
            for path,binding in self._bindings.items():
                proof=self._proofs.get(path)
                try:
                    valid=(path not in self._account_changes and proof is not None and proof['_binding_nonce']==binding['nonce']
                        and 0<=now-proof['sampled_at_ns']<=60_000_000_000
                        and _sha(_raw(self.core.parent,self.core.parent/path))==binding['sha256'])
                except Exception: valid=False
                if not valid: complete=False;continue
                row={key:copy.deepcopy(value) for key,value in proof.items() if key!='_binding_nonce'}
                row['generation']=self._generation;accounts.append(row)
            yield {'version':1,'core':str(self.core.resolve()),'complete':complete,'generation':self._generation,
                   'bots':list(self.bots),'baseline':self.baseline,
                   'processes':copy.deepcopy(sorted(self._processes.values(),key=lambda r:r['pid'])),
                   'accounts':sorted(accounts,key=lambda r:r['path'])}
            self._sync_revocations()
            if self._coverage_check is not None:
                try:self._coverage_check()
                except BaseException:
                    self.revoke_coverage()
                    raise
            if self._account_changes != denials_at_entry:
                raise ValueError('account denial changed during capture')
            if self._fault.is_set() and not fault_at_entry:
                raise ValueError('host coverage revoked during capture')

    def inspect(self):
        with self._mutex():
            return {'sealed':self._sealed,'coverage_revoked':self._tainted or self._fault.is_set(),'registered_processes':len(self._processes),
                    'bound_configs':len(self._bindings),'cached_account_proofs':len(self._proofs),
                    'pending_account_changes':len(self._account_changes),
                    'production_entrypoints_connected':False,'production_apply_supported':False}
