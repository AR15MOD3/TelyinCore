'Frozen host preparation for optional visual inspection.\n\nlaunch hands VisionHost preparation to the configured production consumer.\nThe owner captures Bot/engine identity; an I/O worker must not reread active\nselection. Runtime enablement and actual inference acceptance are separate.\n'
from dataclasses import dataclass, replace
import json
from pathlib import Path
from threading import Lock
from typing import Callable


def immutable_profile(profile):
    # Profile.from_dict historically retains JSON lists despite tuple hints.
    # Canonicalize only their container type, preserving every declared value.
    return replace(profile, efforts=tuple(profile.efforts),
                   effort_suggestions=tuple(profile.effort_suggestions))


def check_cancelled(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise RuntimeError('visual operation cancelled; outcome unconfirmed; do not retry')


@dataclass(frozen=True)
class FrozenInspection:
    bot: str
    load: Callable  # owned worker reads/validates frozen declarations, no inference yet


class VisionHost:
    """Construct once during assembly; freeze cached declared selections on owner loop.

    A changed on-disk declaration is rejected before inference, not silently
    substituted. Refreshing Engine then explicitly calling again is a new call.
    Secrets are read only in owned preparation from the pinned engine root and
    alias; they never enter evidence, repr, status, or trace fields.
    """

    def __init__(self, engine, *, bot, inspect_profile, inspect_local):
        self.engine = engine
        self.source_root = Path(engine.root)
        self.root = Path(engine.root).resolve()  # Assembly-time I/O only.
        self.bot = bot
        self.inspect_profile, self.inspect_local = inspect_profile, inspect_local
        if not isinstance(bot, str) or not bot:
            raise ValueError('visual host requires its explicit Bot')

    def prepare(self, *, bot, local, profile, config, active=False):
        from .engine import Profile, ENV_NAMES, _coerce, _default_for, _as_text
        if bot != self.bot or Path(self.engine.root) != self.source_root:
            raise ValueError('visual host Bot/root changed')
        engine = self.engine
        if not engine._state_lock.acquire(blocking=False):
            raise RuntimeError('engine is busy; no visual preparation started')
        try:
            name = engine._current if local or active else profile
            selected = (immutable_profile(engine._draft_profile(name))
                        if name is not None else None)
            if not local and (not isinstance(selected, Profile)
                              or selected.multimodal is not True or not selected.model):
                raise ValueError('所选档案没有启用图片输入或尚未选模型')
            # Explicit copies, not Engine.effective/current/profile inside a worker.
            env = dict(engine._env)
            raw = env.get(ENV_NAMES['max_tokens'])
            if raw is not None and raw.strip() != '':
                max_tokens = _coerce('max_tokens', raw)
            else:
                raw = getattr(selected, 'max_tokens', None)
                max_tokens = (_coerce('max_tokens', raw) if _as_text(raw).strip()
                              else _default_for('max_tokens'))
        finally:
            engine._state_lock.release()
        encoded = json.dumps(config, ensure_ascii=False, allow_nan=False)
        root, profile_fn, local_fn = self.root, self.inspect_profile, self.inspect_local
        consumed, once = False, Lock()

        def load(cancelled):
            nonlocal consumed
            with once:
                if consumed:
                    raise RuntimeError('visual preparation is single use')
                consumed = True
            check_cancelled(cancelled)
            from .engine import Engine
            source = Engine(root, env=env)  # Pinned source only, no live Engine/selector.
            if (selected is not None
                    and immutable_profile(source.profile(selected.name)) != selected):
                raise RuntimeError('visual declaration changed; '
                                   'refresh before an explicit new call')
            check_cancelled(cancelled)
            secrets = source._read_secret_lines() if not local else {}

            class FrozenEngine:
                def profile(self, requested):
                    if selected is None or requested != selected.name:
                        raise ValueError('visual profile binding mismatch')
                    return selected

                def _read_secret_lines(self):
                    return dict(secrets)

            used, call_lock = False, Lock()

            def infer(image, question, cancel):
                nonlocal used
                with call_lock:
                    if used:
                        raise RuntimeError('visual inference is single use; no retry')
                    used = True
                check_cancelled(cancel)
                if local:
                    result = local_fn(json.loads(encoded), image, question,
                                      max_tokens=int(max_tokens), cancelled=cancel)
                else:
                    result = profile_fn(FrozenEngine(), selected.name, image, question,
                                        cancelled=cancel)
                # Preserve a returned dependency even if cancellation won while
                # its producer ran. The owner-loop retains it before discarding
                # the cancelled worker result; never stringify live work.
                import inspect
                if inspect.isawaitable(result):
                    return result
                check_cancelled(cancel)
                return str(result)

            return infer

        return FrozenInspection(bot, load)
