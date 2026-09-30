"""Eventos que emite M3 (índice §6; payloads en M0 §2.10). Ningún payload lleva el token de confirmación."""

from typing import Literal

from agent_core.domain import (
    Action,
    ActionCancelled,
    ActionCancelledPayload,
    ActionConfirmed,
    ActionConfirmedPayload,
    ActionDispatched,
    ActionDispatchedPayload,
    ActionVerified,
    ActionVerifiedPayload,
    Fingerprint,
    InvalidationReason,
    RunState,
    ToolCalled,
    ToolCalledPayload,
)
from agent_core.ports import Clock, IdKind, IdSource

ConfirmSource = Literal["understand", "button"]


class EventFactory:
    """Arma la envoltura común (id, run, turno, sesión, release, instante) con los puertos inyectados.

    `seq`, `prev_hash` y `hash` quedan vacíos: los asigna M11 (a través del `EventRecorder` o de M4)."""

    def __init__(self, ids: IdSource, clock: Clock) -> None:
        self._ids = ids
        self._clock = clock

    def _envelope(self, state: RunState, turn_id: str | None) -> dict[str, object]:
        return {
            "event_id": self._ids.new_id(IdKind.event),
            "run_id": state.run_id,
            "turn_id": turn_id,
            "session_id": state.session_id,
            "release": state.release,
            "ts": self._clock.now(),
        }

    def confirmed(self, state: RunState, turn_id: str | None, action_id: str,
                  source: ConfirmSource) -> ActionConfirmed:
        payload = ActionConfirmedPayload(action_id=action_id, source=source)
        return ActionConfirmed.model_validate({**self._envelope(state, turn_id), "payload": payload})

    def cancelled(self, state: RunState, turn_id: str | None, action_id: str,
                  reason: InvalidationReason) -> ActionCancelled:
        payload = ActionCancelledPayload(action_id=action_id, reason=reason)
        return ActionCancelled.model_validate({**self._envelope(state, turn_id), "payload": payload})

    def dispatched(self, state: RunState, turn_id: str | None, action: Action,
                   args_fp: Fingerprint | None) -> ActionDispatched:
        payload = ActionDispatchedPayload(action_id=action.action_id, tool=action.tool, args_fp=args_fp)
        return ActionDispatched.model_validate({**self._envelope(state, turn_id), "payload": payload})

    def tool_called(self, state: RunState, turn_id: str | None, payload: ToolCalledPayload) -> ToolCalled:
        return ToolCalled.model_validate({**self._envelope(state, turn_id), "payload": payload})

    def verified(self, state: RunState, turn_id: str | None, action_id: str,
                 result: Literal["verified", "failed", "unavailable"],
                 readback_call_id: str) -> ActionVerified:
        payload = ActionVerifiedPayload(action_id=action_id, result=result, readback_call_id=readback_call_id)
        return ActionVerified.model_validate({**self._envelope(state, turn_id), "payload": payload})
