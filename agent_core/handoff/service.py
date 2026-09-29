"""`HandoffService` (M10 §2–3). Único emisor de `escalated` y `handoff_resolved`;
construye `handoff_created`."""

from collections.abc import Sequence

from agent_core.domain import (
    ActionState,
    Agent,
    Awaiting,
    EngineEvent,
    Escalated,
    EscalatedPayload,
    EscalationRequest,
    HandoffCreatedPayload,
    Message,
    OutboxMessage,
    Outcome,
    RunState,
    Template,
    to_jsonable,
)
from agent_core.handoff.builder import build_minimal_packet, build_packet
from agent_core.handoff.errors import HandoffPreconditionError
from agent_core.handoff.packet import HandoffPacket, HandoffRecord
from agent_core.handoff.projection import Projector
from agent_core.handoff.recorder import EventRecorder, append_events
from agent_core.handoff.texts import default_handoff_message
from agent_core.ports import (
    AuthzPort,
    Clock,
    IdKind,
    IdSource,
    KeyProvider,
    RegistryPort,
    UnitOfWork,
    UnitOfWorkFactory,
)
from agent_core.views import ViewService

_PENDING = (ActionState.proposed, ActionState.confirmed)


class HandoffService:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, registry: RegistryPort, views: ViewService,
                 authz: AuthzPort, keys: KeyProvider, clock: Clock, ids: IdSource,
                 record: EventRecorder = append_events) -> None:
        self._uow_factory = uow_factory
        self._registry = registry
        self._views = views
        self._authz = authz
        self._clock = clock
        self._ids = ids
        self._record = record
        self._projector = Projector(views, keys, ids)

    # --- escalate --------------------------------------------------------------------------------------

    def escalate(self, state: RunState, request: EscalationRequest, events_so_far: Sequence[EngineEvent], *,
                 uow: UnitOfWork, turn_id: str | None = None
                 ) -> tuple[RunState, list[EngineEvent], OutboxMessage, Message]:
        """Persiste el paquete en la UoW del turno y devuelve lo que M4 guarda en esa misma transacción."""
        self._check_can_escalate(state)
        now = self._clock.now()
        handoff_ref = self._ids.new_id(IdKind.handoff)
        packet = self._packet(state, request, handoff_ref, events_so_far)
        uow.put_handoff(handoff_ref, to_jsonable(HandoffRecord(packet=packet)))

        closed = state.model_copy(update={
            "status": "escalated", "outcome": Outcome.escalated, "handoff_ref": handoff_ref,
            "closed_at": now, "last_activity_at": now, "inactive_after": None,
            "awaiting": Awaiting.none, "awaiting_node_id": None, "pending_offer": None,
        })
        event = Escalated(
            event_id=self._ids.new_id(IdKind.event), run_id=state.run_id, turn_id=turn_id,
            session_id=state.session_id, release=state.release, ts=now,
            payload=EscalatedPayload(reason_code=request.reason_code, target_queue=request.target_queue,
                                     priority=request.priority, handoff_ref=handoff_ref),
        )
        outbox = OutboxMessage(
            message_id=self._ids.new_id(IdKind.message), type="handoff_created", run_id=state.run_id,
            payload=to_jsonable(HandoffCreatedPayload(
                handoff_ref=handoff_ref, run_id=state.run_id, target_queue=request.target_queue,
                priority=request.priority, reason_code=request.reason_code, language=state.locale,
                reportable_attrs={k: v for k, v in sorted(state.principal.attrs.items())
                                  if k in self._authz.reportable_attrs()},
            )),
            created_at=now,
        )
        return closed, [event], outbox, self._closing_message(state)

    @staticmethod
    def _check_can_escalate(state: RunState) -> None:
        if state.status != "open":
            raise HandoffPreconditionError(f"{state.run_id}: solo se escala un run abierto ({state.status})")
        if any(a.state in _PENDING for a in state.actions):
            raise HandoffPreconditionError(
                f"{state.run_id}: invalida las acciones pendientes antes de escalar")

    def _packet(self, state: RunState, request: EscalationRequest, handoff_ref: str,
                events: Sequence[EngineEvent]) -> HandoffPacket:
        try:
            return build_packet(state=state, request=request, handoff_ref=handoff_ref, events=events,
                                projector=self._projector, views=self._views)
        except Exception:
            return build_minimal_packet(state=state, request=request, handoff_ref=handoff_ref)

    def _closing_message(self, state: RunState) -> Message:
        text = default_handoff_message(state.locale)
        try:
            agent = self._registry.get(state.agent, Agent)
            template = self._registry.get(agent.templates.handoff.require_exact(), Template)
            custom = template.locales.get(state.locale)
            if custom and not template.reads:
                text = custom
        except Exception:
            pass
        return Message(kind="template", text=text, locale=state.locale)
