"""Cooperating, Bot-owned final-path guard for the existing self-note writer.

No new writer, emotion trigger, file capability or knowledge update is granted.
It is not a hostile same-user filesystem isolation primitive.
"""
from contextlib import contextmanager
from pathlib import Path
from runtime_identity import safe_path
from .source_guard import lease


@contextmanager
def self_note_boundary(home):
    root=Path(home).absolute()
    target=root/'悬着的事'/'selfnote.jsonl'
    def verify():
        safe_path(root,target)
        if target.resolve()!=root.resolve()/'悬着的事'/'selfnote.jsonl':
            raise ValueError('self_note final target escaped Bot home')
        if target.exists() and target.stat().st_nlink!=1:
            raise ValueError('self_note hard-linked target refused')
    verify()
    with lease(root):
        verify()
        yield verify
        verify()
