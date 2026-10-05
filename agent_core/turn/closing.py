"""Cierre del run, escalamiento y aplicación del resultado de M2 (m04 §3.1 paso 12, §3.4, §3.5, §3.6).

M4 es el único emisor de `run_closed` (índice §6): todo cierre pasa por aquí."""

from datetime import datetime

from agent_core.actions import ActionManager
from agent_core.domain import (
    Awaiting,
    EngineError,
    EscalationRequest,
    InvalidationReason,
    Outcome,
    ProblemCode,
    RunState,
)
from agent_core.handoff import HandoffService
from agent_core.interpreter import StepOutcome, Stop
from agent_core.ports import AuditSink, Clock, RegistryPort
from agent_core.turn.events import ClosedBy
from agent_core.turn.frame import TurnFrame
from agent_core.turn.intents import offer_next


def closed_state(state: RunState, outcome: Outcome, closed_by: ClosedBy, now: datetime) -> RunState:
    """Estado de un run cerrado por M4 (`closed`): único para el turno y el barrido (m04 §3.6)."""
    update: dict[str, object] = {
        "status": "closed",
        "outcome": outcome,
        "closed_at": now,
        "last_activity_at": now,
        "inactive_after": None,
        "awaiting": Awaiting.none,
        "awaiting_node_id": None,
        "pending_offer": None,
    }
    if closed_by == "flow":
        update["active_flow"] = None
    return state.model_copy(update=update)


class Closer:
    def __init__(
        self,
        *,
        actions: ActionManager,
        handoff: HandoffService,
        audit: AuditSink,
        clock: Clock,
        registry: RegistryPort,
    ) -> None:
        self._actions = actions
        self._handoff = handoff
        self._audit = audit
        self._clock = clock
        self._registry = registry

    def invalidate(self, frame: TurnFrame, reason: InvalidationReason) -> None:
        """Cancela las acciones `proposed`/`confirmed` (M3 §3.6); sus eventos van al buffer."""
        state, events = self._actions.invalidate(frame.state, reason, turn_id=frame.turn_id)
        frame.state = state
        frame.buffer.add(*events)

    def close_run(self, frame: TurnFrame, outcome: Outcome, closed_by: ClosedBy) -> None:
        """`status=closed`, `outcome` y `run_closed`. `abandoned` solo lo asigna M4; `escalated` solo M10."""
        if outcome is Outcome.escalated:
            raise EngineError(ProblemCode.internal_error, "escalated lo cierra M10, no close_run")
        frame.state = closed_state(frame.state, outcome, closed_by, self._clock.now())
        frame.buffer.add(frame.events.run_closed(frame.state, frame.turn_id, outcome, closed_by))
        frame.closed = True
        frame.confirmation = None
        frame.step_up = None

    def escalate(
        self, frame: TurnFrame, request: EscalationRequest, closed_by: ClosedBy = "escalation"
    ) -> None:
        """Invalida acciones, entrega el paquete a M10 y deja estado, evento, outbox y `run_closed` en la
        misma transacción del turno."""
        self.invalidate(frame, InvalidationReason.escalated)
        events_so_far = [*self._audit.read(frame.state.run_id), *frame.buffer.peek()]
        closed, events, outbox, message = self._handoff.escalate(
            frame.state, request, events_so_far, uow=frame.uow, turn_id=frame.turn_id
        )
        frame.uow.enqueue_outbox(outbox)
        frame.buffer.add(*events)
        frame.messages.append(message)
        frame.state = closed
        frame.buffer.add(frame.events.run_closed(closed, frame.turn_id, Outcome.escalated, closed_by))
        frame.closed = True
        frame.confirmation = None
        frame.step_up = None

    def apply_outcome(self, frame: TurnFrame, outcome: StepOutcome) -> None:
        """Paso 12: traduce el resultado de `advance` en cierre, escalamiento, oferta o espera."""
        frame.state = outcome.state
        frame.advanced = True
        frame.messages.extend(outcome.messages)
        frame.buffer.add(*outcome.events)
        frame.rejected.extend(outcome.rejected_drafts)
        frame.confirmation = outcome.confirmation
        frame.step_up = outcome.step_up
        frame.stop = outcome.stop
        frame.output = outcome.output
        frame.suggestions = outcome.suggestions
        if outcome.transfer is not None:
            # ADR 0021: the pointer stays on the `transfer` node; the engine validates the request and then
            # transfers or follows `rejected` (`TurnEngine._resolve_transfer`).
            frame.pending_transfer = outcome.transfer
            return
        if outcome.escalation is not None:
            self.escalate(frame, outcome.escalation)
        elif outcome.stop is Stop.terminal and outcome.end_outcome is not None:
            frame.state = frame.state.model_copy(update={"active_flow": None, "node_attempts": {}})
            if frame.state.mode == "conversational" and offer_next(frame, self._registry):
                return
            self.close_run(frame, outcome.end_outcome, "flow")
        elif frame.state.mode == "task":
            # M1 G0-16 garantiza que un flow task no espera; si ocurre, es un bug: el run escala.
            request = EscalationRequest(
                reason_code="validation_failed",
                target_queue=frame.agent.default_target_queue,
                priority="normal",
            )
            self.escalate(frame, request)
