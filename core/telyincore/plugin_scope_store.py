"""Bot-private plugin choices/config, independent of shared installed code.

Trusted host passes bots_root + stable Bot ID. This storage layer does not grant
lifecycle authority, load plugins, migrate shared settings or start processes.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time


class ScopeError(ValueError):
    pass


def path_key(path):
    """Compare Windows extended-length spelling without accepting redirects."""
    text=os.fspath(path)
    if os.name=='nt':
        if text.startswith('\\\\?\\UNC\\'):text='\\\\'+text[8:]
        elif text.startswith('\\\\?\\') and len(text)>6 and text[5:7]==':\\':text=text[4:]
    return os.path.normcase(os.path.normpath(text))


def identifier(value):
    if (not isinstance(value, str) or not value or len(value) > 64
            or value != value.strip() or value.endswith(('.', ' '))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value in ('.', '..')
            or value.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}):
        raise ScopeError('invalid stable Bot ID')
    return value


def plugin_name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}',value):
        raise ScopeError('invalid plugin name')
    if value.endswith('.') or value in ('.','..'):
        raise ScopeError('invalid plugin name')
    identifier(value)  # also reject Windows device aliases
    return value


class PluginScopeStore:
    def __init__(self, bots_root, bot, *, config_aliases=None, runtime_policy=None):
        self.bot = identifier(bot)
        self.bots = Path(bots_root)
        if not self.bots.is_absolute():
            raise ScopeError('absolute trusted Bot parent required')
        if runtime_policy is not None:
            # Lazy import: policy imports storage identifiers, never a new type copy.
            from .plugin_runtime_policy import PluginRuntimePolicy, root_key
            if (type(runtime_policy) is not PluginRuntimePolicy
                    or runtime_policy.settings_root != root_key(self.bots)
                    or runtime_policy.bot_id != self.bot):
                raise ScopeError('runtime policy does not match exact Bot settings scope')
        self._runtime_policy = runtime_policy
        self.root = self.bots / self.bot / 'infinity' / 'plugins'
        self.state = self.root / 'state.json'
        self.config_aliases={plugin_name(name):Path(path) for name,path in (config_aliases or {}).items()}
        for path in self.config_aliases.values():
            if not path.is_absolute():raise ScopeError('absolute host configuration alias required')
        self._safe(self.root)

    @property
    def runtime_policy(self):
        """Read-only host policy reference; not permission to run a plugin."""
        return self._runtime_policy

    def _safe(self, path):
        path = Path(path)
        resolved=path.resolve()
        if path_key(resolved) != path_key(path.absolute()):
            raise ScopeError('redirected Bot scope rejected')
        if path != self.root and self.root not in path.parents and path not in self.config_aliases.values():
            raise ScopeError('path outside Bot plugin scope')
        return path

    @contextmanager
    def _locked(self):
        self._safe(self.root)
        self.root.mkdir(parents=True,exist_ok=True)
        lock = self._safe(self.root / '.write.lock')
        stream = lock.open('a+b')
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0,2)
                if stream.tell()==0:
                    stream.write(b'0');stream.flush()
                deadline=time.monotonic()+10
                while True:
                    stream.seek(0)
                    try: msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);break
                    except OSError:
                        if time.monotonic()>=deadline: raise ScopeError('Bot settings lock timeout')
                        time.sleep(.01)
            else:
                import fcntl
                deadline=time.monotonic()+10
                while True:
                    try: fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:
                        if time.monotonic()>=deadline: raise ScopeError('Bot settings lock timeout')
                        time.sleep(.01)
            acquired=True
            yield
        finally:
            if acquired and os.name=='nt':
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            stream.close()

    def _read(self,path,limit):
        path=self._safe(path)
        try:
            with path.open('rb') as stream: raw=stream.read(limit+1)
        except FileNotFoundError:return None
        if len(raw)>limit:raise ScopeError('Bot settings exceed size limit')
        def pairs(items):
            result={}
            for key,value in items:
                if key in result:raise ScopeError('duplicate Bot settings key')
                result[key]=value
            return result
        try: value=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=pairs)
        except (UnicodeError,ValueError) as exc:raise ScopeError('malformed Bot settings') from exc
        if not isinstance(value,dict):raise ScopeError('Bot settings must be an object')
        return value

    def read_state(self):
        value=self._read(self.state,128*1024)
        if value is None:return {'schema':1,'bot_id':self.bot,'revision':0,'enabled':{}}
        if (type(value.get('schema')) is not int or value.get('schema')!=1 or value.get('bot_id')!=self.bot
                or type(value.get('revision')) is not int or value['revision']<0
                or not isinstance(value.get('enabled'),dict)):
            raise ScopeError('Bot plugin state identity/schema mismatch')
        for name,on in value['enabled'].items():
            plugin_name(name)
            if type(on) is not bool:raise ScopeError('plugin switch must be boolean')
        if 'initialization' in value:
            receipt=value['initialization']
            if (not isinstance(receipt,dict) or type(receipt.get('schema')) is not int
                    or receipt['schema']!=1 or receipt.get('bot_id')!=self.bot
                    or not isinstance(receipt.get('source'),dict)
                    or type(receipt.get('legacy_applied')) is not bool):
                raise ScopeError('invalid initialization receipt')
        return value

    def _publish(self,path,value,limit):
        path=self._safe(path)
        raw=(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
        if len(raw)>limit:raise ScopeError('Bot settings exceed size limit')
        path.parent.mkdir(parents=True,exist_ok=True)
        self._backup(path)
        fd,temporary=tempfile.mkstemp(prefix='.scope-stage-',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
            self._safe(path)
            os.replace(temporary,path)
        finally:Path(temporary).unlink(missing_ok=True)
        return hashlib.sha256(raw).hexdigest()

    def _backup(self,path):
        if not path.exists():return
        self._archive_bytes(path.read_bytes())

    def _archive_bytes(self,old):
        # Content addressing deduplicates within this Bot without long nested
        # config.json directories exceeding Windows' legacy MAX_PATH limit.
        backup=self._safe(self.root/'.backups'/(hashlib.sha256(old).hexdigest()+'.json'))
        backup.parent.mkdir(parents=True,exist_ok=True)
        # Convert only after normal scope/redirect checks. Extended spelling
        # changes Win32 path handling, never the permitted target or basename.
        if os.name == 'nt':
            text = str(backup.absolute())
            if not text.startswith('\\\\?\\'):
                backup = Path('\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text)
        try:
            with backup.open('xb') as stream:
                stream.write(old);stream.flush();os.fsync(stream.fileno())
        except FileExistsError:
            if backup.read_bytes()!=old:raise ScopeError('backup checksum mismatch')

    def update(self,changes,*,expected_revision=None):
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision<0):
            raise ScopeError('invalid expected revision')
        if not isinstance(changes,dict):raise ScopeError('changes must be object')
        for name,on in changes.items():
            plugin_name(name)
            if type(on) is not bool:raise ScopeError('plugin switch must be boolean')
        with self._locked():
            value=self.read_state()
            if expected_revision is not None and value['revision']!=expected_revision:
                raise ScopeError('stale Bot state revision')
            value=dict(value,revision=value['revision']+1,enabled=dict(value['enabled'],**changes))
            self._publish(self.state,value,128*1024)
            return value

    def config_path(self,name):
        name=plugin_name(name)
        return self._safe(self.config_aliases.get(name,self.root/name/'config.json'))

    def initialize(self, seed, *, provenance, legacy_seed=None, legacy_raw=None):
        """One atomic receipt + state publication. Existing private choices win.

        The host supplies reviewed non-secret provenance and initial switches;
        this API grants no plugin lifecycle or account authority. A receipt is
        never re-applied on restart, even if the original seed later changes.
        """
        if not isinstance(seed,dict):raise ScopeError('initial choices must be object')
        for name,on in seed.items():
            plugin_name(name)
            if type(on) is not bool:raise ScopeError('plugin switch must be boolean')
        legacy_seed={} if legacy_seed is None else legacy_seed
        if not isinstance(legacy_seed,dict):raise ScopeError('legacy choices must be object')
        for name,on in legacy_seed.items():
            plugin_name(name)
            if type(on) is not bool:raise ScopeError('plugin switch must be boolean')
        if legacy_raw is not None:
            if not isinstance(legacy_raw,bytes) or len(legacy_raw)>128*1024:
                raise ScopeError('invalid legacy snapshot')
            if json.loads(legacy_raw.decode('utf-8-sig'))!=legacy_seed:
                raise ScopeError('legacy snapshot does not match switches')
        if not isinstance(provenance,dict):raise ScopeError('migration provenance required')
        # Detached, JSON-only metadata, never a live host object.
        receipt=json.loads(json.dumps(provenance,allow_nan=False))
        with self._locked():
            value=self.read_state()
            if 'initialization' in value:
                old=value['initialization']
                if (not isinstance(old,dict) or old.get('schema')!=1
                        or old.get('bot_id')!=self.bot):
                    raise ScopeError('invalid initialization receipt')
                return value
            legacy_applied=not self.state.exists() and bool(legacy_seed)
            if legacy_raw is not None and not self.state.exists():
                # Archive before state+receipt publication. An interrupted
                # attempt may leave a harmless checksum-addressed backup;
                # retry deduplicates it, never a half-applied receipt.
                self._archive_bytes(legacy_raw)
            enabled=dict(legacy_seed) if legacy_applied else {}
            enabled.update(seed)
            enabled.update(value['enabled'])
            value=dict(value,revision=value['revision']+1,enabled=enabled,
                       initialization={'schema':1,'bot_id':self.bot,'source':receipt,
                                       'legacy_applied':legacy_applied})
            self._publish(self.state,value,128*1024)
            return value

    def forget(self,name):
        plugin_name(name)
        with self._locked():
            value=self.read_state()
            enabled=dict(value['enabled']);enabled.pop(name,None)
            value=dict(value,revision=value['revision']+1,enabled=enabled)
            self._publish(self.state,value,128*1024)
            return value

    def read_config(self,name):
        # No shared/package config fallback, no cross-Bot credential migration.
        return self._read(self.config_path(name),2*1024*1024)

    def save_config(self,name,value,*,expected_sha256=None):
        if not isinstance(value,dict):raise ScopeError('config must be object')
        with self._locked():
            path=self.config_path(name)
            self.read_config(name)  # refuse to overwrite malformed private config
            if expected_sha256 is not None:
                actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ''
                if actual!=expected_sha256:raise ScopeError('stale Bot config checksum')
            return self._publish(path,value,2*1024*1024)

    def restore_config(self,name,old_raw,*,expected_sha256):
        """Restore exact bytes only if no later writer has changed our result."""
        with self._locked():
            path=self.config_path(name)
            actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ''
            if actual!=expected_sha256:raise ScopeError('configuration changed after save; rollback refused')
            self._backup(path)
            if old_raw is None:
                path.unlink(missing_ok=True)
                return
            fd,temporary=tempfile.mkstemp(prefix='.scope-restore-',dir=path.parent)
            try:
                with os.fdopen(fd,'wb') as stream:
                    stream.write(old_raw);stream.flush();os.fsync(stream.fileno())
                self._safe(path);os.replace(temporary,path)
            finally:Path(temporary).unlink(missing_ok=True)
