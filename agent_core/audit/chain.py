"""Cadena de hash por run (M11 §3.1, decisiones 1 y 2). Funciones puras: sin I/O, sin reloj, sin IDs."""

from typing import Any

from pydantic import TypeAdapter

from agent_core.domain import AnyEvent, EngineEvent, canonical_bytes, dumps, loads, sha256_hex, to_jsonable
from agent_core.domain.base import Model

type ChainedEvent = EngineEvent  # seq, prev_hash y hash no nulos

_GENESIS_PREFIX = b"agentcore:"
_EVENTS: TypeAdapter[Any] = TypeAdapter(AnyEvent)


class ChainError(ValueError):
    """Los eventos no se pueden encadenar (run ajeno, ya encadenados o `last` sin hash)."""


class ChainCheck(Model):
    ok: bool
    broken_at: int | None = None
    reason: str | None = None


def genesis_hash(run_id: str) -> str:
    """`hash_0`: constante por run. Es el `prev_hash` del evento con `seq = 0`."""
    return sha256_hex(_GENESIS_PREFIX + run_id.encode())


def event_hash(event: EngineEvent, prev_hash: str) -> str:
    """`sha256(JCS(evento sin el campo hash) ‖ prev_hash)`. Incluye `seq` y `prev_hash` del evento."""
    body = to_jsonable(event)
    body.pop("hash", None)
    return sha256_hex(canonical_bytes(body) + prev_hash.encode("ascii"))


def chain_events(run_id: str, events: list[EngineEvent], last: EngineEvent | None) -> list[EngineEvent]:
    if last is None:
        seq, prev = 0, genesis_hash(run_id)
    else:
        if last.seq is None or last.hash is None:
            raise ChainError("el último evento del run no está encadenado")
        seq, prev = last.seq + 1, last.hash
    chained: list[EngineEvent] = []
    for event in events:
        if event.run_id != run_id:
            raise ChainError(f"evento {event.event_id} de otro run")
        if event.seq is not None or event.prev_hash is not None or event.hash is not None:
            raise ChainError(f"evento {event.event_id} ya encadenado")
        linked = event.model_copy(update={"seq": seq, "prev_hash": prev})
        linked = linked.model_copy(update={"hash": event_hash(linked, prev)})
        chained.append(linked)
        seq, prev = seq + 1, linked.hash or ""
    return chained


def check_chain(run_id: str, events: list[EngineEvent]) -> ChainCheck:
    """`ok`, o `broken_at(seq)` con el `seq` esperado en la primera posición inconsistente."""
    prev = genesis_hash(run_id)
    for index, event in enumerate(events):
        problem = None
        if event.run_id != run_id:
            problem = "run distinto"
        elif event.seq != index:
            problem = "seq no contiguo"
        elif event.prev_hash != prev:
            problem = "prev_hash no coincide"
        elif event.hash is None or event.hash != event_hash(event, prev):
            problem = "hash no coincide"
        if problem is not None:
            return ChainCheck(ok=False, broken_at=index, reason=problem)
        prev = event.hash or ""
    return ChainCheck(ok=True)


def event_to_json(event: EngineEvent) -> str:
    return dumps(event)


def event_from_json(text: str) -> EngineEvent:
    result: EngineEvent = _EVENTS.validate_python(loads(text))
    return result
