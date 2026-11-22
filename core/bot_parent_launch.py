"""Opt-in trusted parent factory with an explicit, frozen, single-use OS plan.

No environment enumeration, files/config/key reads, shell, PATH resolution,
discovery/adoption or automatic cleanup. Use ONLY inside the host birth window;
this callable itself grants no authority and cannot prove descendant coverage.
Arbitrary trusted Python can bypass it; existing callable factories remain valid.
"""
import os
import hashlib
import hmac
import json
from pathlib import Path
import re
import subprocess
from threading import Lock


class FixedProcessFactory:
    def __init__(self, argv, cwd, env, *, independent_owner=None):
        if (type(argv) is not list or not 1 <= len(argv) <= 64
                or any(type(v) is not str or not v or '\0' in v or len(v) > 4096 for v in argv)
                or sum(map(len, argv)) > 16384
                or not Path(argv[0]).is_absolute()
                or argv[0].startswith(('\\\\', '//'))
                or (os.name == 'nt' and Path(argv[0]).suffix.casefold() != '.exe')
                or not isinstance(cwd, (str, Path)) or not str(cwd) or '\0' in str(cwd)
                or len(str(cwd)) > 4096 or str(cwd).startswith(('\\\\', '//'))
                or not Path(cwd).is_absolute()
                or type(env) is not dict or len(env) > 64):
            raise ValueError('invalid explicit parent launch plan')
        names = set()
        size = 0
        for key, value in env.items():
            if (type(key) is not str or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', key)
                    or type(value) is not str or '\0' in value or len(value) > 4096
                    or key.casefold() in names):
                raise ValueError('invalid explicit parent environment')
            names.add(key.casefold())
            size += len(key) + len(value) + 2
        if size > 32768:
            raise ValueError('explicit parent environment limit')
        # Immutable primitives, not references to mutable caller input.
        self._argv, self._cwd, self._env = tuple(argv), str(cwd), tuple(sorted(env.items()))
        self._starting = Lock()
        self._attempted = False
        self._process = None
        self._independent_owner = independent_owner
        if independent_owner is not None:
            from bot_independent_owner import IndependentProcessOwner
            if (type(independent_owner) is not IndependentProcessOwner
                    or independent_owner.plan() != self.plan()
                    or independent_owner.state != 'planned'):
                raise ValueError('exact independent HOST owner and plan required')

    def plan(self):
        """Private HOST copy, never serialize into a public/RPC status report."""
        return dict(argv=list(self._argv), cwd=self._cwd, env=dict(self._env))

    @property
    def owned_process(self):
        # A retained handle is NOT proof of native identity, absence or coverage.
        return (self._process if self._independent_owner is None
                else self._independent_owner.process)

    def __call__(self):
        if not self._starting.acquire(blocking=False):
            raise ValueError('parent launch already in progress')
        try:
            if self._attempted:
                raise ValueError('parent launch is single use')
            self._attempted = True  # failures/interruptions never grant a retry
            if self._independent_owner is not None:
                try:
                    return self._independent_owner.start()
                finally:
                    self._process = self._independent_owner.process
            try:
                process = subprocess.Popen(list(self._argv), cwd=self._cwd,
                    env=dict(self._env), shell=False, close_fds=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except Exception:
                raise ValueError('explicit parent launch unavailable') from None
            self._process = process  # retain before broker native commit/reply
            return process
        finally:
            self._starting.release()

    def retire_resources(self):
        """Independent root absence is required before bounded subtree retirement.

        A remote retire request cannot become an implicit stop of a live root.
        Failures retain factory, owner and native handles; no retry/reset grant.
        """
        if self._independent_owner is not None:
            if self.owned_process is None or self.owned_process.poll() is None:
                raise ValueError('independent root not exited')
            self._independent_owner.drain()
            self._independent_owner.assert_releasable()

    def inspect(self):
        return dict(attempted=self._attempted, handle_retained=self._process is not None,
                    environment_policy='explicit-only', production_enabled=False,
                    production_apply_supported=False)


def launch_contract(key, session, core, bot_scope, slot, plan):
    """Session/scope/slot-bound keyed commitment. No raw plan is sent via RPC.

    Exact strings are intentional: an alternate spelling must not silently
    rewrite the actual frozen OS plan. This is not an executable-content hash.
    """
    if (type(key) is not bytes or len(key) != 32 or type(session) is not str
            or not re.fullmatch('[0-9a-f]{32}', session)
            or type(core) is not str or not Path(core).is_absolute()
            or (bot_scope is not None and (type(bot_scope) is not str
                or not Path(bot_scope).is_absolute()))
            or type(slot) is not str or not re.fullmatch('[a-z][a-z0-9-]{0,31}', slot)
            or type(plan) is not dict or set(plan) != {'argv','cwd','env'}):
        raise ValueError('invalid fixed launch contract')
    frozen = FixedProcessFactory(**plan).plan()
    body = dict(version=1, session=session, core=core, bot_scope=bot_scope,
                slot=slot, plan=frozen)
    raw = json.dumps(body, sort_keys=True, ensure_ascii=True,
                     separators=(',', ':'), allow_nan=False).encode('ascii')
    return hmac.new(key, b'bot-fixed-launch-v1\0'+raw, hashlib.sha256).hexdigest()
