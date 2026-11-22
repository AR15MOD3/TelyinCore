"""Read-time emotion dynamics, never a timer, inference job or score writer.

Intensity is a role-state control value, not a measured psychological quantity.
Time reduces activation; it does not prove that a cause has been resolved.
"""
from dataclasses import dataclass
import copy
import datetime as dt
import json


@dataclass(frozen=True)
class Policy:
    enabled: bool = True
    half_life_hours: int = 6
    resolved_half_life_hours: int = 1


def policy(config):
    enabled = config.get("emotion_dynamics", True)
    half = config.get("emotion_half_life_hours", 6)
    resolved = config.get("resolved_half_life_hours", 1)
    if type(enabled) is not bool:
        raise ValueError("emotion_dynamics_requires_bool")
    if any(type(v) is not int or not 1 <= v <= 168 for v in (half, resolved)):
        raise ValueError("emotion_half_life_requires_1_to_168_hours")
    return Policy(enabled, half, resolved)


def project(emotion, now, settings):
    out = copy.deepcopy(emotion)
    then = dt.datetime.fromisoformat(emotion["updated_at"])
    # Clock rollback cannot intensify a state or persist a synthetic timestamp.
    seconds = max(0, (now - then).total_seconds())
    out["stale"] = seconds >= 21600
    if not settings.enabled:
        return out
    half = (settings.resolved_half_life_hours if emotion["resolved"] else settings.half_life_hours)
    effective = emotion["intensity"] * (2 ** (-seconds / (half * 3600)))
    effective = 0.0 if effective < .25 else round(effective, 2)
    out["effective_intensity"] = effective
    out["elapsed_seconds"] = int(seconds)
    out["phase"] = ("resolved" if emotion["resolved"] else "quiet" if effective == 0
                    else "settling" if seconds >= half * 1800 else "active")
    return out


def _brief(emotion):
    # Do not recursively copy past histories, accounts, source quotes or scopes.
    return {"feelings": emotion["feelings"][:3], "intensity": emotion["intensity"],
            "reason": emotion["reason"][:500], "resolved": emotion["resolved"],
            "updated_at": emotion["updated_at"]}


def transition(previous, current, now, settings):
    out = copy.deepcopy(current)
    if not settings.enabled or not previous:
        return out
    if any(previous.get(k) != current.get(k) for k in ("scope", "person")):
        return out
    old = project(previous, now, settings)
    out["recent"] = [*[_brief(r) for r in previous.get("recent", [])[-2:]], _brief(previous)]
    kind = ("resolved" if current["resolved"] else
            "shifted" if not set(previous["feelings"]) & set(current["feelings"]) else
            "strengthened" if current["intensity"] > old["effective_intensity"] else
            "eased" if current["intensity"] < old["effective_intensity"] else "continued")
    out["transition"] = {"from_effective_intensity": old["effective_intensity"],
                         "to_intensity": current["intensity"], "kind": kind}
    return out


def assessment_snapshot(reader):
    value = json.loads(reader())
    emotion = value.get("emotion")
    if not emotion:
        return None
    return {"feelings": emotion["feelings"][:3],
            "effective_intensity": emotion.get("effective_intensity", emotion["intensity"]),
            "resolved": emotion["resolved"], "phase": emotion.get("phase", "legacy"),
            "reason": emotion["reason"][:500],
            "recent": [_brief(r) for r in emotion.get("recent", [])[-3:]]}
