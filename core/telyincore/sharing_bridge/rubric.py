"""Deterministic structured-interest scoring; NOT a natural-language assessor.

Tags/recipient relation are host-verified fixture signals. Desire expresses the
agent's preference, not access authority. Exposure reviews remain separate.
"""
from dataclasses import dataclass, replace
from math import ceil
from .sharing import Interest, key, level


@dataclass(frozen=True)
class Signals:
    topics: frozenset[str]
    preferred_topics: frozenset[str]
    directly_related: bool
    desire: int


def rate(candidate, state, signals: Signals, *, now_ms):
    if (not level(signals.desire) or type(signals.directly_related) is not bool
            or len(signals.topics) > 32 or len(signals.preferred_topics) > 32
            or not all(key(t) for t in signals.topics | signals.preferred_topics)):
        raise ValueError("invalid_structured_signals")
    matches = len(signals.topics & signals.preferred_topics)
    relevance = 4 if signals.directly_related else min(4, matches + 1)
    history = [a for a in state.attempts if a.bot == candidate.source.bot
               and a.destination == candidate.destination
               and a.event == candidate.source.event and a.status == "accepted"]
    known = {f for a in history for f in a.facts}
    facts = set(candidate.source.facts)
    novelty = (ceil(4 * len(facts - known) / len(facts)) if facts
               else (0 if history else 4))
    age = now_ms - candidate.source.observed_ms
    timing = 4 if 0 <= age < 1_800_000 else 3 if age < 7_200_000 else 2 if age < 21_600_000 else 0
    if age < 0:
        timing = 0
    return replace(candidate, interest=Interest(relevance, signals.desire, novelty, timing))
