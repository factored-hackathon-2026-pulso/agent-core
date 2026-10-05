"""`TurnEngine`: orquesta un turno (m04 §3.1). Una sola UoW por turno; los dos commits por escritura los
hace M3 con `uow_factory`."""

import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from agent_core.actions import ActionManager
from agent_core.domain import (
    Agent,
    Awaiting,
    CollectNode,
    Command,
    EngineError,
    EngineEvent,
    EntityKind,
    EntityRef,
    EscalationRequest,
    Flow,
    IllegalTransition,
    InvalidationReason,
    Locale,
    OnBehalfOf,
    Outcome,
    Principal,
    ProblemCode,
    RefSpec,
    Release,
    ResponseEmitted,
    RunInput,
    RunOrigin,
    RunResult,
    RunState,
    Slot,
    SubjectRef,
    TranscriptRef,
    TransferContract,
    TurnInProgress,
    TurnInput,
    TurnResult,
    canonical_bytes,
    packet_problem,
    sha256_hex,
)
from agent_core.handoff import HandoffService
from agent_core.interpreter import NO_RESUME, Resume, StepContext, Stop, advance, begin_turn, start_flow
from agent_core.ports import (
    AuditSink,
    AuthzPort,
    Clock,
    IdKind,
    IdSource,
    RegistryPort,
    UnitOfWork,
    UnitOfWorkFactory,
)
from agent_core.turn.buffer import EventBuffer, TurnEventSink
from agent_core.turn.closing import Closer
from agent_core.turn.config import TurnConfig
from agent_core.turn.events import CommandInfo, TurnEvents
from agent_core.turn.frame import TurnFrame
from agent_core.turn.handlers import Env, clarify_now, resolve_pending_confirm, run_global_handlers
from agent_core.turn.intents import (
    answer_offer,
    claim_slots,
    mentioned_flows,
    queue_mentioned,
)
from agent_core.turn.metering import StageMeter
from agent_core.turn.ports import (
    EventChain,
    GuardsPort,
    RuntimeFactory,
    TraceIds,
    TransferOutcome,
    TurnRecorderPort,
    TurnRuntime,
    TurnScope,
    TurnSpan,
    TurnTelemetry,
    UnderstandOutcome,
    UnderstandPort,
    UnderstandRequest,
)
from agent_core.turn.recovery import position_at_verify
from agent_core.turn.refs import pinned_ref
from agent_core.turn.results import awaiting_for, build_turn_result
from agent_core.turn.sweep import Sweeper, SweepReport
from agent_core.turn.telemetry import NO_SPAN, NoTurnTelemetry, observed_turn
from agent_core.turn.templates import render_engine
from agent_core.turn.transfer import Transferer, TransferPlan, event_target

_LOG = logging.getLogger("agent_core.turn")


class TurnEngine:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        registry: RegistryPort,
        clock: Clock,
        ids: IdSource,
        guards: GuardsPort,
        understand: UnderstandPort,
        actions: ActionManager,
        handoff: HandoffService,
        recorder: TurnRecorderPort,
        chain: EventChain,
        audit: AuditSink,
        runtimes: RuntimeFactory,
        trace: TraceIds,
        config: TurnConfig | None = None,
        authz: AuthzPort | Any | None = None,
        telemetry: TurnTelemetry | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._registry = registry
        self._clock = clock
        self._ids = ids
        self._guards = guards
        self._understand = understand
        self._actions = actions
        self._handoff = handoff
        self._recorder = recorder
        self._chain = chain
        self._audit = audit
        self._runtimes = runtimes
        self._trace = trace
        self._config = config or TurnConfig()
        self._authz = authz
        self._telemetry: TurnTelemetry = telemetry or NoTurnTelemetry()
        self._events = TurnEvents(ids, clock)
        self._closer = Closer(actions=actions, handoff=handoff, audit=audit, clock=clock, registry=registry)
        self._env = Env(closer=self._closer, registry=registry)
        self._transferer = Transferer(registry, ids, authz, self._config.max_transfers_per_session)
        self._sweeper = Sweeper(
            uow_factory=uow_factory,
            registry=registry,
            clock=clock,
            ids=ids,
            actions=actions,
            chain=chain,
            config=self._config,
        )

    # --- piezas comunes -----------------------------------------------------------------------------

    def _new_frame(
        self,
        uow: UnitOfWork,
        state: RunState,
        turn_id: str,
        entry: str,
        client_turn_id: str | None,
        agent: Agent,
        runtime: TurnRuntime,
        meter: StageMeter,
        release: Any,
        span: TurnSpan = NO_SPAN,
    ) -> TurnFrame:
        buffer = EventBuffer(turn_id)
        frame = TurnFrame(
            uow=uow,
            state=state,
            turn_id=turn_id,
            entry="start_run" if entry == "start_run" else "turn",
            client_turn_id=client_turn_id,
            agent=agent,
            release=release,
            runtime=runtime,
            meter=meter,
            buffer=buffer,
            events=self._events,
            initial_run_cost=state.budgets_used.run_cost,
            span=span,
        )
        frame.sink = TurnEventSink(
            buffer, self._chain, lambda: self._ensure_started(frame), observe=frame.span.record
        )
        return frame

    @staticmethod
    def _scope(state: RunState, turn_id: str, entry: Literal["start_run", "turn"]) -> TurnScope:
        """What the turn's telemetry is about (m04 §3.9): ids and closed-list values, never a payload."""
        return TurnScope(
            run_id=state.run_id,
            turn_id=turn_id,
            session_id=state.session_id,
            release=state.release,
            agent=state.agent,
            entry=entry,
            principal_type=state.principal.type.value,
            locale=state.locale,
        )

    def _ensure_started(self, frame: TurnFrame) -> None:
        """Materializa `turn_started` sin salida de M6 si alguien vuelca antes de las guardas."""
        if frame.buffer.turn_started_reserved and not frame.buffer.turn_started_filled:
            frame.buffer.fill(
                self._events.turn_started(frame.state, frame.turn_id, frame.client_turn_id, guards=None)
            )

    def _step_ctx(self, frame: TurnFrame) -> StepContext:
        assert frame.sink is not None
        return replace(
            frame.runtime.step,
            release=frame.release,
            agent=frame.agent,
            locale=frame.state.locale,
            degraded=frame.degraded,
            record=frame.sink.record,
            turn_id=frame.turn_id,
        )

    def _flow(self, release: Any, ref: RefSpec) -> Flow:
        return self._registry.get(pinned_ref(release, EntityKind.flow, ref), Flow)

    def _advance(self, frame: TurnFrame) -> None:
        """Paso 11: `advance` y su traducción (paso 12). Una transferencia se resuelve aquí (ADR 0021)."""
        with frame.meter.stage("flow"):
            outcome = advance(frame.state, self._step_ctx(frame), frame.resume)
            self._closer.apply_outcome(frame, outcome)
            self._resolve_transfer(frame)

    def _resolve_transfer(self, frame: TurnFrame) -> None:
        """Spec §5.2: validate the request of a `transfer` node. A rejection emits `transfer_rejected` and the
        flow goes on along `rejected` in this same turn; a valid transfer emits `run_transferred` and
        closes the origin (`transferred`, `closed_by=transfer`). The target run opens from `transfer_plan`."""
        request = frame.pending_transfer
        if request is None:
            return
        frame.pending_transfer = None
        transfer_id = self._transferer.new_transfer_id()
        with frame.span.transfer(transfer_id) as span:  # ADR 0021 D9: the span covers the validation
            plan = self._transferer.validate(frame, request, transfer_id)
            if isinstance(plan, str):
                # `event_target` is what `transfer_rejected.to_agent` echoes, so span and event agree (U5).
                span.finish(TransferOutcome(
                    outcome="rejected", to_agent=event_target(request), reason_code=plan))
            else:
                span.finish(TransferOutcome(
                    outcome="transferred", to_agent=plan.target_ref.id, to_release_id=plan.release.id))
                frame.transfer_link = span.link
        if isinstance(plan, str):
            frame.buffer.add(self._events.transfer_rejected(
                frame.state, frame.turn_id, transfer_id, event_target(request), plan, request.snapshot))
            frame.state = self._follow(frame.state, request.node_id, "rejected")
            frame.resume = NO_RESUME
            # Bounded: `rejected` cannot reach this `transfer` again without a node that waits (M1 G0-04).
            outcome = advance(frame.state, self._step_ctx(frame), frame.resume)
            self._closer.apply_outcome(frame, outcome)
            self._resolve_transfer(frame)
            return
        frame.buffer.add(self._events.run_transferred(
            frame.state, frame.turn_id, transfer_id=plan.transfer_id, target_ref=plan.target_ref,
            release_id=plan.release.id, to_run_id=plan.to_run_id, reason=plan.packet.reason,
            packet_fp=plan.packet_fp, snapshot=plan.snapshot))
        self._closer.close_run(frame, Outcome.transferred, "transfer")
        frame.transfer_plan = plan

    def _follow(self, state: RunState, node_id: str, key: str) -> RunState:
        """Moves the pointer from the node M2 stopped on along `next[key]`."""
        assert state.active_flow is not None
        flow = self._registry.get(state.active_flow.flow, Flow)
        node = next(n for n in flow.nodes if n.id == node_id)
        target = node.next.get(key)
        if target is None:
            raise IllegalTransition(f"el nodo {node_id} no tiene transición para {key!r}")
        active = state.active_flow.model_copy(update={"node_id": target})
        return state.model_copy(update={"active_flow": active})

    def _record(self, frame: TurnFrame) -> None:
        """Paso 13: transcript en vista `model` y `transcript_fp` en los `response_emitted` del turno."""
        final_model = "\n\n".join(m.text for m in frame.messages)
        refs = self._recorder.record_turn(
            frame.state.run_id, frame.turn_id, frame.text_model, final_model, frame.rejected
        )
        self._fill_transcript_fp(frame, refs)

    @staticmethod
    def _fill_transcript_fp(frame: TurnFrame, refs: list[TranscriptRef]) -> None:
        if len(refs) < 2:
            return
        fingerprint = refs[-1].fingerprint  # M11: `[user, *rejected, final]`

        def fill(event: EngineEvent) -> EngineEvent:
            if isinstance(event, ResponseEmitted) and event.payload.transcript_fp is None:
                payload = event.payload.model_copy(update={"transcript_fp": fingerprint})
                return event.model_copy(update={"payload": payload})
            return event

        frame.buffer.transform(fill)

    def _cap_repairs(self, frame: TurnFrame) -> None:
        """Tope global (m04 §3.2): aclaraciones + reintentos de `collect` + `unclear` de `confirm`. Superar
        `max_repair_turns_per_run` escala `low_confidence`; es lo único que escala por reparación."""
        state = frame.state
        if frame.closed or state.status != "open":
            return
        if state.repair_turns_used > frame.agent.max_repair_turns_per_run:
            request = EscalationRequest(
                reason_code="low_confidence",
                target_queue=frame.agent.default_target_queue,
                priority="normal",
            )
            with frame.meter.stage("flow"):
                self._closer.escalate(frame, request)

    def _finish(self, frame: TurnFrame, *, record: bool, store_result: bool = True) -> TurnResult:
        """Pasos 12 (tope de reparación), 13, 13b y 14: registrar, medir y persistir. El `commit()` lo hace
        quien llama."""
        self._ensure_started(frame)
        self._cap_repairs(frame)
        if record:
            with frame.meter.stage("response"):
                self._record(frame)
        state = frame.state
        now = self._clock.now()
        stop = frame.stop if state.active_flow is not None else None
        awaiting, node = awaiting_for(stop, state)
        update: dict[str, Any] = {
            "awaiting": awaiting,
            "awaiting_node_id": node,
            "token_map": frame.runtime.sealed_token_map() or state.token_map,
        }
        if state.status == "open":
            update["last_activity_at"] = now
            if state.mode == "conversational":  # m04 §3.6: el barrido solo mira runs conversacionales
                update["inactive_after"] = now + frame.agent.inactivity_ttl
        state = state.model_copy(update=update)
        completed = self._events.turn_completed(
            state,
            frame.turn_id,
            frame.entry,
            frame.client_turn_id,
            duration_ms=frame.meter.duration_ms(),
            stages=frame.meter.stages(),
            degraded=frame.degraded,
            awaiting=awaiting,
        )
        saved = frame.uow.save_run(state, expected_version=state.state_version)
        chained = [*frame.buffer.drain(), completed]
        self._chain.append(frame.uow, saved.run_id, chained)
        frame.span.record(chained)  # telemetry reads, never writes, the turn's events (U4)
        cost = max(saved.budgets_used.run_cost - frame.initial_run_cost, Decimal("0"))
        frame.uow.add_usage(saved.principal.key, cost, now)
        result = build_turn_result(frame, saved, self._trace.current(frame.turn_id))
        if frame.entry == "turn":
            frame.uow.release_turn(saved.run_id, frame.turn_id)
        frame.state = saved
        plan = frame.transfer_plan
        if plan is not None:  # ADR 0021: the origin is saved and chained; the same turn goes on in the target
            result = self._continue_in_target(frame, result, plan)
        if store_result and frame.client_turn_id is not None:
            # After a transfer the combined result is stored under the target run, the one
            # `find_run_by_session` returns to a retry (Review Focus 1).
            frame.uow.put_turn_result(result.run_id, frame.client_turn_id, result)
        return result

    def _continue_in_target(self, frame: TurnFrame, source: TurnResult, plan: TransferPlan) -> TurnResult:
        """Spec §5.2 steps 4-7: same turn, same session, principal and subject; a new run pinned to the
        specialist's release, linked to the origin chain by the hash of its `turn_completed` (P2). The
        packet slots enter as `validated`; the trigger text is processed as the target's turn (P4). It all
        goes in the turn's unit of work: the origin was saved first, the target is saved in its own
        `_finish`, and one commit applies both or neither."""
        origin_state = frame.state
        head = frame.uow.last_event(origin_state.run_id)
        if head is None or head.hash is None:
            raise IllegalTransition("una transferencia necesita la cadena de hash de M11 en el run origen")
        origin = RunOrigin(
            kind="transfer",
            transfer_id=plan.transfer_id,
            from_run_id=origin_state.run_id,
            from_agent=origin_state.agent,
            from_release_id=origin_state.release,
            from_event_hash=head.hash,
            depth=plan.depth,
        )
        slots = {
            name: Slot(value=value, status="validated", source_turn=1)
            for name, value in plan.packet.slots.items()
        }
        target = self._new_state(
            run_id=plan.to_run_id,
            session_id=origin_state.session_id,
            release=plan.release,
            agent_ref=plan.target_ref,
            agent=plan.target,
            principal=origin_state.principal,
            on_behalf_of=origin_state.on_behalf_of,
            subject=origin_state.subject,
            locale=origin_state.locale,
            slots=slots,
            origin=origin,
            turn_count=0,  # `_process` counts this turn as the target's first
        )
        prelude = [
            self._events.run_started(
                target, plan.target_ref, self._reportable(origin_state.principal), origin=origin
            ),
            self._events.transfer_received(
                target, frame.turn_id, plan.transfer_id, list(slots), plan.packet_fp
            ),
        ]
        turn = frame.turn
        assert turn is not None  # P6: a transfer during `start_run` is rejected (`no_turn`)
        # The target's span covers its `_process`; the one commit of the turn comes later (m04 §3.9).
        links = () if frame.transfer_link is None else (frame.transfer_link,)
        with observed_turn(self._telemetry, self._scope(target, frame.turn_id, "turn"), links) as span:
            result = self._process(
                frame.uow,
                target,
                origin_state.principal,
                origin_state.on_behalf_of,
                turn,
                frame.turn_id,
                StageMeter(self._clock),
                prelude=prelude,
                store_result=False,
                span=span,
            )
        if isinstance(result, EngineError):  # a fresh run cannot expire; never commit half a transfer
            raise result
        return result.model_copy(update={"messages": [*source.messages, *result.messages]})

    # --- handle_turn --------------------------------------------------------------------------------

    def handle_turn(
        self, principal: Principal, on_behalf_of: OnBehalfOf | None, turn: TurnInput
    ) -> TurnResult:
        meter = StageMeter(self._clock)
        turn_id = self._ids.new_id(IdKind.turn)
        leased: str | None = None
        try:
            with self._uow_factory() as uow:
                found = uow.find_run_by_session(turn.session_id)
                if found is None:
                    raise EngineError(ProblemCode.not_found, "sesión")
                cached = self._cached_result(uow, found, turn)
                if cached is not None:  # paso 1: un duplicado devuelve lo guardado aunque el run ya cerró
                    return cached
                if found.status != "open":  # 410 antes que 409: reintentar no serviría de nada
                    raise EngineError(ProblemCode.run_closed, "run cerrado")
                try:
                    uow.acquire_turn(found.run_id, turn_id, self._clock.now(), self._config.lease_ttl)
                except TurnInProgress as exc:
                    raise EngineError(ProblemCode.turn_in_progress, "turno en curso") from exc
                leased = found.run_id
                cached = self._cached_result(uow, found, turn)
                if cached is not None:  # el otro turno commiteó entre mi lectura y mi lease
                    uow.release_turn(found.run_id, turn_id)
                    uow.commit()
                    leased = None
                    return cached
                state = uow.load_run(found.run_id) or found  # estado fresco tras tomar el lease
                if state.status != "open":
                    raise EngineError(ProblemCode.run_closed, "run cerrado")
                # m04 §3.9: the turn's span covers its processing and its commit; an `EngineError` the turn
                # commits (expiry, P2) leaves the span inside it, so the span carries its problem code.
                with observed_turn(self._telemetry, self._scope(state, turn_id, "turn")) as span:
                    outcome = self._process(
                        uow, state, principal, on_behalf_of, turn, turn_id, meter, span=span
                    )
                    uow.commit()
                    leased = None
                    if isinstance(outcome, EngineError):
                        raise outcome
                return outcome
        except Exception:
            if leased is not None:
                self._release_quietly(leased, turn_id)
            raise

    @staticmethod
    def _cached_result(uow: UnitOfWork, found: RunState, turn: TurnInput) -> TurnResult | None:
        """Paso 1. The result of this `client_turn_id` under the session's current run or, after a transfer,
        under an earlier run of the session (a retry of a turn the origin answered)."""
        cached = uow.get_turn_result(found.run_id, turn.client_turn_id)
        if cached is not None or found.session_id is None:
            return cached
        for run in uow.list_runs_by_session(found.session_id):
            if run.run_id != found.run_id:
                cached = uow.get_turn_result(run.run_id, turn.client_turn_id)
                if cached is not None:
                    return cached
        return None

    def _release_quietly(self, run_id: str, turn_id: str) -> None:
        """Una excepción (no una caída) libera el lease con una UoW propia para que el reintento no espere
        al TTL. Una caída real (`BaseException`) no pasa por aquí: el lease vence solo."""
        try:
            with self._uow_factory() as uow:
                uow.release_turn(run_id, turn_id)
                uow.commit()
        except Exception as exc:  # the retry will wait for the lease TTL: leave a trace, type only (I3)
            _LOG.warning("lease sin liberar run_id=%s turn_id=%s causa=%s",
                         run_id, turn_id, type(exc).__name__)

    @staticmethod
    def _refresh_auth(state: RunState, presented: Principal) -> RunState:
        """ADR 0010: el turno siguiente a un step-up llega con una credencial elevada del mismo principal.

        Solo se toma `auth` (M9 ya validó firma y `principal_mismatch`); nunca la identidad, los roles ni los
        scopes. Si la clave no coincide, no se cambia nada."""
        if presented.key != state.principal.key or presented.auth == state.principal.auth:
            return state
        principal = state.principal.model_copy(update={"auth": presented.auth})
        return state.model_copy(update={"principal": principal})

    def _process(
        self,
        uow: UnitOfWork,
        state: RunState,
        principal: Principal,
        on_behalf_of: OnBehalfOf | None,
        turn: TurnInput,
        turn_id: str,
        meter: StageMeter,
        *,
        prelude: Sequence[EngineEvent] = (),
        store_result: bool = True,
        span: TurnSpan = NO_SPAN,
    ) -> TurnResult | EngineError:
        """Pasos 3 a 14. Devuelve un `EngineError` (ya commiteable) cuando el turno cierra el run sin
        procesar el mensaje (abandono, P2). `prelude` abre la cadena del run destino de una transferencia
        (`run_started`, `transfer_received`) antes de `turn_started`."""
        state = begin_turn(state.model_copy(update={"turn_count": state.turn_count + 1}), self._clock)
        state = self._refresh_auth(state, principal)
        runtime = self._runtimes.open(state, principal, on_behalf_of)
        agent, release = runtime.step.agent, runtime.step.release
        frame = self._new_frame(
            uow, state, turn_id, "turn", turn.client_turn_id, agent, runtime, meter, release, span
        )
        frame.turn = turn
        frame.text_model = runtime.model_text(turn.text)  # C1: el texto crudo no sale de aquí
        frame.buffer.add_prelude(*prelude)
        frame.buffer.reserve_turn_started()
        if self._registry.release_status(state.release) == "revoked":  # paso 3
            request = EscalationRequest(
                reason_code="release_revoked", target_queue=agent.default_target_queue, priority="normal"
            )
            with frame.meter.stage("flow"):
                self._closer.escalate(frame, request, "revocation")
            return self._finish(frame, record=True, store_result=store_result)
        if self._expired(frame):  # paso 4 (P2): se cierra y el mensaje no se procesa
            return EngineError(ProblemCode.run_closed, "run vencido por inactividad")
        if self._recover(frame):  # paso 5
            return self._finish(frame, record=True, store_result=store_result)
        frame.state, events = self._actions.expire_tokens(frame.state, turn_id=turn_id)  # paso 6
        frame.buffer.add(*events)
        frame.tokens_expired = bool(events)
        if self._guard(frame):  # paso 7: unsupported / tamaño → plantilla, sin Understand ni flow
            return self._finish(frame, record=True, store_result=store_result)
        understood = self._understand_step(frame)  # paso 8
        if frame.closed:  # Understand superó su tope de llamadas: el turno ya escaló
            return self._finish(frame, record=True, store_result=store_result)
        with frame.meter.stage("flow"):  # pasos 9 a 12
            self._decide_and_advance(frame, understood)
        return self._finish(frame, record=True, store_result=store_result)

    def _decide_and_advance(self, frame: TurnFrame, understood: UnderstandOutcome | None) -> None:
        """Pasos 9-12 tras Understand: manejadores globales, elección de flow o resolución del `confirm`,
        y `advance`."""
        confirm_pending = frame.state.awaiting is Awaiting.confirmation
        if understood is None:  # botón: sin Understand y sin manejadores; nunca `unclear` (T-M4-05)
            self._advance_turn(frame)
            return
        handled = run_global_handlers(self._env, frame, understood, confirm_pending=confirm_pending)
        if handled is not None:
            if handled.advance:
                self._advance_turn(frame)
            return
        if confirm_pending:
            queued = queue_mentioned(self._registry, frame, understood)
            frame.resume = resolve_pending_confirm(understood)
            self._advance_turn(frame)
            if queued and not frame.closed:
                self._acknowledge(frame)
            return
        self._select_flow(frame, understood)

    def _select_flow(self, frame: TurnFrame, outcome: UnderstandOutcome) -> None:
        """Paso 10 (m04 §3.3): oferta pendiente, arranque de flow, intenciones pendientes y `continue`."""
        state = frame.state
        if state.pending_offer is not None and state.active_flow is None:
            flow = answer_offer(frame, self._registry, outcome)
            if flow is not None:
                frame.state = start_flow(
                    frame.state.model_copy(update={"pending_offer": None, "awaiting": Awaiting.none}), flow
                )
                frame.resume = NO_RESUME
                self._advance_turn(frame)
            return
        claim_slots(frame, outcome)
        mentioned = mentioned_flows(self._registry, frame.release, outcome)
        if state.active_flow is None:
            if outcome.command is not Command.start_flow or not mentioned:
                clarify_now(self._env, frame, outcome)
                return
            first = mentioned[0][0]
            frame.state = start_flow(frame.state, first)
            queued = queue_mentioned(self._registry, frame, outcome, skip=first.id)
            entry = first.nodes[0]
            captures = isinstance(entry, CollectNode) and entry.config.capture_start
            turn = frame.turn
            assert turn is not None
            # M2 D16: el texto que arranca el flow responde al primer `collect` con `capture_start`
            frame.resume = Resume("slot_answer", turn.text, carry=True) if captures else NO_RESUME
            self._advance_turn(frame)
        else:
            queued = queue_mentioned(self._registry, frame, outcome, skip=state.active_flow.flow.id)
            answers_slot = frame.state.awaiting is Awaiting.slot and outcome.command in (
                Command.continue_,
                Command.affirm,
                Command.deny,
            )
            turn = frame.turn
            assert turn is not None
            frame.resume = Resume("slot_answer", turn.text) if answers_slot else NO_RESUME
            self._advance_turn(frame)
        if queued and not frame.closed:
            self._acknowledge(frame)

    def _advance_turn(self, frame: TurnFrame) -> None:
        """`advance` y contadores de reparación de M4: un `unclear` de texto suma (C14); el botón no."""
        if frame.tokens_expired and frame.state.awaiting is Awaiting.confirmation:
            # Un token vencido nunca confirma: el `confirm` vuelve a proponer con token nuevo (rev. 2).
            frame.resume = NO_RESUME
        text_unclear = frame.resume.kind == "confirm_answer" and frame.resume.value == "unclear"
        self._advance(frame)
        if text_unclear:
            frame.state = frame.state.model_copy(
                update={"repair_turns_used": frame.state.repair_turns_used + 1}
            )

    def _acknowledge(self, frame: TurnFrame) -> None:
        frame.messages.append(
            render_engine(
                self._registry, frame.release, frame.agent.templates.pending_ack, frame.state.locale
            )
        )

    def _understand_step(self, frame: TurnFrame) -> UnderstandOutcome | None:
        """Paso 8. Un botón `confirm` no pasa por Understand: `resume = confirm_answer`, `source=button`."""
        turn = frame.turn
        assert turn is not None
        state = frame.state
        if turn.confirm is not None:
            command = Command.affirm if turn.confirm.answer == "yes" else Command.deny
            frame.buffer.add(
                self._events.command_emitted(
                    state, frame.turn_id, CommandInfo(command=command), source="button"
                )
            )
            frame.resume = Resume("confirm_answer", turn.confirm.answer, token=turn.confirm.token)
            return None
        request = UnderstandRequest(
            text_model=frame.text_model,
            state=state,
            release=frame.release,
            agent=frame.agent,
            locale=state.locale,
            awaiting_confirmation=state.awaiting is Awaiting.confirmation,
            current_node=state.awaiting_node_id,
            turn_id=frame.turn_id,
            step=frame.runtime.step,
        )
        with frame.meter.stage("understand"):
            outcome = self._understand.run(request)
        frame.buffer.add(*outcome.events)  # `decision_made`, lo emite M5
        info = CommandInfo(
            command=outcome.command,
            flow=outcome.flow,
            interrupt=outcome.interrupt,
            additional_flows=list(outcome.additional_flows),
            above_threshold=dict(outcome.above_threshold),
            decision_id=outcome.decision_id,
        )
        frame.buffer.add(self._events.command_emitted(state, frame.turn_id, info, source="understand"))
        self._charge_understand(frame, outcome)
        return outcome

    def _charge_understand(self, frame: TurnFrame, outcome: UnderstandOutcome) -> None:
        """Las llamadas de Understand se cuentan aparte de `turn_model_calls`; pasar el tope escala.

        Sus tokens y su costo entran a `run_tokens`/`run_cost` (M4 §16), igual que los de M2 (`charge_model`);
        `add_usage` toma el delta de `run_cost`, así que el principal no paga dos veces."""
        used = frame.state.budgets_used
        counted = used.model_copy(update={
            "turn_understand_calls": used.turn_understand_calls + outcome.model_calls,
            "run_tokens": used.run_tokens + outcome.tokens,
            "run_cost": used.run_cost + outcome.cost_usd,
        })
        frame.state = frame.state.model_copy(update={"budgets_used": counted})
        if counted.turn_understand_calls > self._config.max_understand_calls_per_turn:
            request = EscalationRequest(
                reason_code="budget_exceeded", target_queue=frame.agent.default_target_queue,
                priority="normal")
            self._closer.escalate(frame, request)

    def _guard(self, frame: TurnFrame) -> bool:
        """Paso 7. Devuelve `True` si el turno termina aquí con una plantilla del motor."""
        turn = frame.turn
        assert turn is not None
        state = frame.state
        with frame.meter.stage("guards"):
            result, events = self._guards.run(
                frame.text_model,
                state,
                frame.agent,
                frame.release,
                turn.lang,
                False,
                turn_id=frame.turn_id,
            )
        frame.buffer.fill(
            self._events.turn_started(state, frame.turn_id, frame.client_turn_id, guards=result.to_output())
        )
        frame.buffer.add(*events)  # `injection_flagged` lo construye M6 y lo agrega M4
        update: dict[str, Any] = {}
        if result.lang.decision != "unsupported":
            update["locale"] = result.lang.locale
        if result.injection.flagged:
            frame.degraded = True
            update["degraded_turns"] = [*state.degraded_turns, state.turn_count]
        if update:
            frame.state = state.model_copy(update=update)
        templates = frame.agent.templates
        if result.lang.decision == "unsupported":
            ref, locale = templates.unsupported_language, frame.agent.default_locale
        elif not result.size_ok:
            ref, locale = templates.input_too_large, frame.state.locale
        else:
            return False
        frame.messages.append(render_engine(self._registry, frame.release, ref, locale))
        return True

    def _recover(self, frame: TurnFrame) -> bool:
        """Devuelve `True` si la recuperación terminó el flow (C9): el turno acaba ahí y el mensaje no se
        procesa. Si el flow queda esperando al usuario, el mensaje se procesa normalmente."""
        pending = self._actions.pending_recovery(frame.state)
        if not pending:
            return False
        if frame.state.active_flow is None:
            raise IllegalTransition(f"{frame.state.run_id}: acción en executing sin flow activo")
        flow = self._registry.get(frame.state.active_flow.flow, Flow)
        frame.state = position_at_verify(frame.state, pending, flow)
        frame.resume = NO_RESUME
        self._advance(frame)
        return frame.closed or frame.state.active_flow is None or frame.stop is Stop.terminal

    def _expired(self, frame: TurnFrame) -> bool:
        """Evalúa `now − last_activity_at > inactivity_ttl` (estricto), emite `expiry_evaluated` en cada
        turno (C12) y, si venció, abandona: invalida acciones, `abandoned`, `turn_completed` y persiste."""
        now = self._clock.now()
        ttl = frame.agent.inactivity_ttl
        expired = now - frame.state.last_activity_at > ttl
        frame.buffer.add(self._events.expiry_evaluated(frame.state, frame.turn_id, now, ttl, expired=expired))
        if not expired:
            return False
        self._closer.invalidate(frame, InvalidationReason.abandoned)
        self._closer.close_run(frame, Outcome.abandoned, "abandonment")
        self._finish(frame, record=False, store_result=False)  # el reintento recibe 410
        return True

    def sweep(self, now: datetime) -> SweepReport:
        """Barrido periódico de inactividad (delega en `Sweeper`)."""
        return self._sweeper.sweep(now)

    # --- start_run ----------------------------------------------------------------------------------

    def start_run(
        self, principal: Principal, on_behalf_of: OnBehalfOf | None, run_input: RunInput
    ) -> RunResult:
        """Crea el run (release fijada), emite `run_started` y arranca `entry_flow`. En modo task avanza
        hasta un terminal. No toma lease (el `run_id` es nuevo).

        Idempotente por `(principal, idempotency_key)`: el mismo body devuelve el `RunResult` ya creado y otro
        body con la misma clave es `409 idempotency_conflict`; la misma clave con otra petición aún en
        curso es `409 idempotency_in_progress` (reintentable). La clave va en la transacción del run."""
        body = run_input.model_copy(update={"idempotency_key": ""})
        body_hash = sha256_hex(canonical_bytes(body))
        principal_key, idem_key = principal.key, run_input.idempotency_key
        with self._uow_factory() as uow:  # reserva inmediata: la misma clave no corre dos veces a la vez
            prior = uow.reserve_run_idempotency(principal_key, idem_key, body_hash, self._clock.now(),
                                                self._config.lease_ttl)
        if prior is not None:
            prior_hash, prior_result = prior
            if prior_hash != body_hash:
                raise EngineError(ProblemCode.idempotency_conflict)
            return prior_result
        try:
            return self._start_reserved(principal, on_behalf_of, run_input, body_hash)
        except BaseException:
            with self._uow_factory() as uow:  # el run no commiteó: la clave vuelve a estar libre
                uow.release_run_idempotency(principal_key, idem_key)
            raise

    def _start_reserved(
        self, principal: Principal, on_behalf_of: OnBehalfOf | None, run_input: RunInput, body_hash: str
    ) -> RunResult:
        meter = StageMeter(self._clock)
        release = self._registry.resolve_release(run_input.agent, principal)
        version = release.entities.get(EntityKind.agent, {}).get(run_input.agent.id)
        if version is None:
            raise EngineError(ProblemCode.not_found, "agente")
        agent_ref = EntityRef(id=run_input.agent.id, version=version)
        agent = self._registry.get(agent_ref, Agent)
        conversational = agent.mode == "conversational"
        locale = run_input.lang if run_input.lang in agent.supported_locales else agent.default_locale
        # Un agente task con `input_schema` valida la entrada contra su contrato: entra como `validated`.
        # Sin él (o conversacional), lo que trae el request sigue siendo una afirmación (`claimed`).
        status: Literal["claimed", "validated"] = "claimed"
        if agent.mode == "task" and agent.input_schema is not None:
            problem = packet_problem(TransferContract(slots=agent.input_schema), run_input.input or {})
            if problem is not None:
                raise EngineError(ProblemCode.invalid_request, f"input: {problem}")
            status = "validated"
        slots = {
            name: Slot(value=value, status=status, source_turn=1)
            for name, value in (run_input.input or {}).items()
        }
        state = self._new_state(
            run_id=self._ids.new_id(IdKind.run),
            session_id=self._ids.new_id(IdKind.session) if conversational else None,
            release=release,
            agent_ref=agent_ref,
            agent=agent,
            principal=principal,
            on_behalf_of=on_behalf_of,
            subject=run_input.subject,
            locale=locale,
            slots=slots,
        )
        turn_id = self._ids.new_id(IdKind.turn)
        state = begin_turn(state, self._clock)
        runtime = self._runtimes.open(state, principal, on_behalf_of)
        with (
            self._uow_factory() as uow,
            observed_turn(self._telemetry, self._scope(state, turn_id, "start_run")) as span,
        ):  # m04 §3.9: the span covers the creation, `advance` and the commit
            frame = self._new_frame(
                uow, state, turn_id, "start_run", None, agent, runtime, meter, release, span
            )
            frame.buffer.add(
                self._events.run_started(state, agent_ref, self._reportable(principal)),
                self._events.turn_started(state, turn_id, None, guards=None),
            )
            frame.state = start_flow(state, self._flow(release, agent.entry_flow))
            self._advance(frame)
            result = self._finish(frame, record=conversational)
            saved = frame.state
            run_result = RunResult(
                run_id=saved.run_id,
                session_id=saved.session_id,
                release=saved.release,
                output=frame.output if not conversational else None,
                status=saved.status,
                outcome=saved.outcome,
                handoff_ref=saved.handoff_ref,
                first_turn=result if conversational else None,
                trace_id=result.trace_id,
            )
            uow.put_run_idempotency(principal.key, run_input.idempotency_key, body_hash, run_result)
            uow.commit()
        return run_result

    def _new_state(
        self,
        *,
        run_id: str,
        session_id: str | None,
        release: Release,
        agent_ref: EntityRef,
        agent: Agent,
        principal: Principal,
        on_behalf_of: OnBehalfOf | None,
        subject: SubjectRef | None,
        locale: Locale,
        slots: dict[str, Slot],
        origin: RunOrigin | None = None,
        turn_count: int = 1,
    ) -> RunState:
        """A new run pinned to `release` (`start_run` and the target of a transfer)."""
        now = self._clock.now()
        conversational = agent.mode == "conversational"
        return RunState(
            run_id=run_id,
            session_id=session_id,
            release=release.id,
            agent=agent_ref,
            principal=principal,
            on_behalf_of=on_behalf_of,
            subject=subject,
            mode=agent.mode,
            locale=locale,
            created_at=now,
            last_activity_at=now,
            inactive_after=now + agent.inactivity_ttl if conversational else None,
            turn_count=turn_count,
            slots=slots,
            origin=origin,
        )

    def _reportable(self, principal: Principal) -> dict[str, str]:
        """The principal attributes `run_started` may carry (`AuthzPort.reportable_attrs`)."""
        reportable: frozenset[str] = (
            self._authz.reportable_attrs() if self._authz is not None else frozenset()
        )
        return {k: v for k, v in sorted(principal.attrs.items()) if k in reportable}
