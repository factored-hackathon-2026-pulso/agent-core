"""Construcción del evento `injection_flagged` (M6 §6; payload en M0 §2.10).

`seq`, `prev_hash` y `hash` quedan vacíos: los asigna M11 cuando M4 agrega el evento a la cadena."""

from typing import Literal

from agent_core.domain import InjectionFlagged, InjectionFlaggedPayload, RunState
from agent_core.guards.models import InjectionResult
from agent_core.ports import Clock, IdKind, IdSource

Scope = Literal["user_text", "untrusted_field"]


class GuardEvents:
    def __init__(self, ids: IdSource, clock: Clock) -> None:
        self._ids = ids
        self._clock = clock

    def injection_flagged(self, state: RunState, turn_id: str | None, result: InjectionResult,
                          scope: Scope) -> InjectionFlagged:
        payload = InjectionFlaggedPayload(signals=list(result.signals), ruleset=result.ruleset, scope=scope)
        return InjectionFlagged.model_validate({
            "event_id": self._ids.new_id(IdKind.event), "run_id": state.run_id, "turn_id": turn_id,
            "session_id": state.session_id, "release": state.release, "ts": self._clock.now(),
            "payload": payload,
        })
