"""Immutable per-call QQ ownership. No ambient events are read by an I/O worker."""
from dataclasses import dataclass
import json
from typing import Any


@dataclass(frozen=True)
class QqToolSnapshot:
    bot: str
    scope: str
    client: Any
    media_client: Any
    tts_client: Any
    generation: str
    privates: frozenset
    groups: frozenset
    proactive: str
    batch_json: str | None
    voice: str = 'default'
    voice_batch: str = ''
    corpus: Any = None
    corpus_hash: str = 'unknown'
    sharing: Any = None
    voice_mode: str = 'on_demand'
    media_dir: Any = None
    media_context: tuple = ()
    private_text_lease: Any = None


from telyincore.batch_snapshot import freeze_batch
