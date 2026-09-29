"""`HandoffService` (M10 §2–3). Único emisor de `escalated` y `handoff_resolved`;
construye `handoff_created`."""

import re
from collections.abc import Sequence
from datetime import timedelta

from agent_core.domain import (
    Action,
    ActionState,
    Agent,
    Awaiting,
    EngineError,
    EngineEvent,
    Escalated,
    EscalatedPayload,
    EscalationRequest,
    HandoffCreatedPayload,
    HandoffResolved,
    HandoffResolvedPayload,
    JsonValue,
    Message,
    OnBehalfOf,
    OutboxMessage,
    Outcome,
    Principal,
    ProblemCode,
    RunState,
    Template,
    TurnInProgress,
    to_jsonable,
)
from agent_core.handoff.builder import build_minimal_packet, build_packet
from agent_core.handoff.errors import HandoffPreconditionError
from agent_core.handoff.packet import HandoffPacket, HandoffQuality, HandoffRecord, Resolution
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
_RESOLUTION_LEASE = timedelta(seconds=30)
_CODE_RE = re.compile(r"[a-z0-9][a-z0-9_:-]{0,63}")
_QUALITIES = frozenset({"useful", "incomplete", "unnecessary"})
_MAX_NOTES = 2000


def _validate_resolution(code: str, quality: str, notes: str | None) -> None:
    if _CODE_RE.fullmatch(code) is None:
        raise EngineError(ProblemCode.invalid_request, "resolution_code inválido")
    if quality not in _QUALITIES:
        raise EngineError(ProblemCode.invalid_request, "handoff_quality inválido")
    if notes is not None and len(notes) > _MAX_NOTES:
        raise EngineError(ProblemCode.invalid_request, "notes demasiado largas")


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
        reportable = self._authz.reportable_attrs()
        outbox = OutboxMessage(
            message_id=self._ids.new_id(IdKind.message), type="handoff_created", run_id=state.run_id,
            payload=to_jsonable(HandoffCreatedPayload(
                handoff_ref=handoff_ref, run_id=state.run_id, target_queue=request.target_queue,
                priority=request.priority, reason_code=request.reason_code, language=state.locale,
                reportable_attrs={k: v for k, v in sorted(state.principal.attrs.items())
                                  if k in reportable},
            )),
            created_at=now,
        )
        return closed, [event], outbox, self._closing_message(state)

    # --- get -------------------------------------------------------------------------------------------

    def get(self, handoff_ref: str, reader: Principal,
            on_behalf_of: OnBehalfOf | None = None) -> dict[str, JsonValue]:
        """Paquete renderizado para `reader`: lo que la política le permite en claro, el resto enmascarado."""
        with self._uow_factory() as uow:
            record = self._load_record(uow, handoff_ref)
            run = self._load_run(uow, record.packet.run_id)
        self._authorize(reader, on_behalf_of, run)
        packet = record.packet
        if not packet.degraded_packet:
            run_id = run.run_id
            facts = [
                view.model_copy(update={"value": self._projector.for_reader(
                    run_id, run.facts[view.name].value, view.name, reader, on_behalf_of)})
                if view.name in run.facts else view
                for view in packet.verified_facts
            ]
            slots = [
                view.model_copy(update={"value": self._projector.for_reader(
                    run_id, run.slots[view.name].value, view.name, reader, on_behalf_of)})
                if view.name in run.slots else view
                for view in packet.claimed_not_verified
            ]
            by_id = {a.action_id: a for a in run.actions}
            actions = [
                view.model_copy(update={"args": self._args_for(run_id, by_id[view.action_id], reader,
                                                               on_behalf_of)})
                if view.action_id in by_id else view
                for view in packet.actions_taken
            ]
            packet = packet.model_copy(update={"verified_facts": facts, "claimed_not_verified": slots,
                                               "actions_taken": actions})
        packet = packet.model_copy(update={"subject": run.subject})  # autorizado sobre ese subject
        out: dict[str, JsonValue] = to_jsonable(packet)
        out["resolution"] = to_jsonable(record.resolution)
        return out

    def _args_for(self, run_id: str, action: Action, reader: Principal,
                  obo: OnBehalfOf | None) -> dict[str, JsonValue]:
        value = self._projector.for_reader(run_id, action.args, action.tool.id, reader, obo)
        return value if isinstance(value, dict) else {}

    # --- record_resolution -----------------------------------------------------------------------------

    def record_resolution(self, handoff_ref: str, reader: Principal, resolution_code: str,
                          handoff_quality: HandoffQuality, notes: str | None = None, *,
                          on_behalf_of: OnBehalfOf | None = None) -> EngineEvent:
        """Registra la resolución del receptor una sola vez: marca el registro y agrega `handoff_resolved`."""
        _validate_resolution(resolution_code, handoff_quality, notes)
        now = self._clock.now()
        with self._uow_factory() as uow:
            record = self._load_record(uow, handoff_ref)
            run = self._load_run(uow, record.packet.run_id)
            self._authorize(reader, on_behalf_of, run)
            # El lease es por run y re-entrante por turn_id: un id único por llamada evita que dos
            # resoluciones concurrentes lo adquieran a la vez.
            lease = f"resolve-{handoff_ref}-{self._ids.new_id(IdKind.turn)}"
            try:
                uow.acquire_turn(run.run_id, lease, now, _RESOLUTION_LEASE)
            except TurnInProgress:
                raise EngineError(ProblemCode.turn_in_progress, "resolución en curso") from None
            record = self._load_record(uow, handoff_ref)  # relee con el lease tomado
            if record.resolution is not None:
                uow.release_turn(run.run_id, lease)
                uow.commit()
                raise EngineError(ProblemCode.handoff_already_resolved, handoff_ref)
            event = HandoffResolved(
                event_id=self._ids.new_id(IdKind.event), run_id=run.run_id, turn_id=None,
                session_id=run.session_id, release=run.release, ts=now,
                payload=HandoffResolvedPayload(handoff_ref=handoff_ref, resolution_code=resolution_code,
                                               handoff_quality=handoff_quality, reader_type=reader.type),
            )
            resolution = Resolution(resolution_code=resolution_code, handoff_quality=handoff_quality,
                                    notes=notes, resolved_at=now, reader_type=reader.type,
                                    reader_id=reader.id)
            resolved = record.model_copy(update={"resolution": resolution})
            uow.put_handoff(handoff_ref, to_jsonable(resolved))
            self._record(uow, run, [event])
            uow.release_turn(run.run_id, lease)
            uow.commit()
            return event

    # --- utilidades compartidas con record_resolution --------------------------------------------------

    @staticmethod
    def _load_record(uow: UnitOfWork, handoff_ref: str) -> HandoffRecord:
        raw = uow.get_handoff(handoff_ref)
        if raw is None:
            raise EngineError(ProblemCode.not_found, handoff_ref)
        return HandoffRecord.model_validate(raw)

    @staticmethod
    def _load_run(uow: UnitOfWork, run_id: str) -> RunState:
        run = uow.load_run(run_id)
        if run is None:
            raise EngineError(ProblemCode.not_found, run_id)
        return run

    def _authorize(self, reader: Principal, obo: OnBehalfOf | None, run: RunState) -> None:
        decision = self._authz.authorize_subject(reader, obo, run.subject)
        if not decision.allowed:
            raise EngineError(ProblemCode.subject_forbidden, decision.reason or "")

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
