(
    'Trusted evidence values only; no grant/approv'
    'al or writer implementation.'
)
from __future__ import annotations
from dataclasses import dataclass
import hashlib, re

@dataclass(frozen=True)
class Message:
    message_id: str
    author: str
    text: str
    trusted: bool = False
    origin: str = 'external'
    recorded_at: str = ''  # Host message timestamp, never inferred from its text.

@dataclass(frozen=True)
class Turn:
    turn_id: str
    scope: str
    messages: tuple[Message, ...]
    prior_messages: tuple[Message, ...] = ()

def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def valid_turn(turn):
    return (
        isinstance(turn, Turn) and isinstance(turn.messages, tuple) and isinstance(turn.turn_id,
            str) and bool(turn.turn_id) and isinstance(turn.scope, str) and bool(turn.scope) and
                all((isinstance(m, Message) and isinstance(m.text, str) and isinstance(m.
                    message_id, str) and bool(m.message_id) and isinstance(m.author, str) and
                        bool(m.author) for m in turn.messages)) and (len({m.message_id for m in
                            turn.messages}) == len(turn.messages))
    )
