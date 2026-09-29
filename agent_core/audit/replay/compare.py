"""Comparación evento a evento (M11 §3.4): excluye el sobre y los campos de medición (M0 §2.10)."""

from itertools import zip_longest

from agent_core.audit.replay.report import Divergence
from agent_core.domain import MEASURED_FIELDS, EngineEvent, JsonValue, to_jsonable

_IGNORED_ENVELOPE = ("event_id", "seq", "prev_hash", "hash", "ts")


def normalize(event: EngineEvent) -> dict[str, JsonValue]:
    data: dict[str, JsonValue] = to_jsonable(event)
    for key in _IGNORED_ENVELOPE:
        data.pop(key, None)
    payload = data.get("payload")
    if isinstance(payload, dict):
        for name in MEASURED_FIELDS.get(str(data.get("type")), frozenset()):
            payload.pop(name, None)
    return data


def first_divergence(recorded: list[EngineEvent], replayed: list[EngineEvent]) -> Divergence | None:
    for index, (expected, actual) in enumerate(zip_longest(recorded, replayed)):
        exp = normalize(expected) if expected is not None else None
        act = normalize(actual) if actual is not None else None
        if exp != act:
            seq = expected.seq if expected is not None and expected.seq is not None else index
            return Divergence(event_seq=seq, expected=exp, actual=act)
    return None
