"""Ayudantes de las pruebas de M11. Solo datos sintéticos."""

from typing import Any

from pydantic import TypeAdapter

from agent_core.domain import AnyEvent, EngineEvent
from tests.m00.samples import SAMPLE_PAYLOADS, make_event

_ADAPTER: TypeAdapter[Any] = TypeAdapter(AnyEvent)
EVENT_TYPES = tuple(SAMPLE_PAYLOADS)


def event(event_type: str = "turn_completed", *, run_id: str = "run-0001", n: int = 1,
          **over: Any) -> EngineEvent:
    """Evento sin encadenar (seq/prev_hash/hash vacíos) de un tipo de M0."""
    raw = {**make_event(event_type), "run_id": run_id, "event_id": f"event-{run_id}-{n:04d}", **over}
    result: EngineEvent = _ADAPTER.validate_python(raw)
    return result


def events_of(run_id: str, count: int) -> list[EngineEvent]:
    """`count` eventos sin encadenar de tipos variados."""
    return [event(EVENT_TYPES[i % len(EVENT_TYPES)], run_id=run_id, n=i + 1) for i in range(count)]
