"""`TurnEngine`: orquesta un turno (m04 §3.1). Una sola UoW por turno; los dos commits por escritura los
hace M3 con `uow_factory`."""

from dataclasses import replace
from decimal import Decimal
from typing import Any

from agent_core.actions import ActionManager
from agent_core.domain import (
    Agent,
    Awaiting,
    Command,
    EngineError,
    EngineEvent,
    EntityKind,
    EntityRef,
    EscalationRequest,
    Flow,
    IllegalTransition,
    InvalidationReason,
    OnBehalfOf,
    Outcome,
    Principal,
    ProblemCode,
    RefSpec,
    ResponseEmitted,
    RunInput,
    RunResult,
    RunState,
    Slot,
    TranscriptRef,
    TurnInProgress,
    TurnInput,
    TurnResult,
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
    TurnRecorderPort,
    TurnRuntime,
    UnderstandOutcome,
    UnderstandPort,
    UnderstandRequest,
)
from agent_core.turn.recovery import position_at_verify
from agent_core.turn.refs import pinned_ref
from agent_core.turn.results import awaiting_for, build_turn_result
from agent_core.turn.templates import render_engine


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
        self._events = TurnEvents(ids, clock)
        self._closer = Closer(actions=actions, handoff=handoff, audit=audit, clock=clock, registry=registry)
        self._env = Env(closer=self._closer, registry=registry)

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
    ) -> TurnFrame:
        buffer = EventBuffer()
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
        )
        frame.sink = TurnEventSink(buffer, self._chain, lambda: self._ensure_started(frame))
        return frame

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
        """Paso 11: `advance` y su traducción (paso 12)."""
        with frame.meter.stage("flow"):
            outcome = advance(frame.state, self._step_ctx(frame), frame.resume)
            self._closer.apply_outcome(frame, outcome)

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
        fingerprint = refs[1].fingerprint

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
        self._chain.append(frame.uow, saved.run_id, [*frame.buffer.drain(), completed])
        cost = max(saved.budgets_used.run_cost - frame.initial_run_cost, Decimal("0")) + frame.cost_usd
        frame.uow.add_usage(saved.principal.key, cost, now)
        result = build_turn_result(frame, saved, self._trace.current(frame.turn_id))
        if store_result and frame.client_turn_id is not None:
            frame.uow.put_turn_result(saved.run_id, frame.client_turn_id, result)
        if frame.entry == "turn":
            frame.uow.release_turn(saved.run_id, frame.turn_id)
        frame.state = saved
        return result

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
                cached = uow.get_turn_result(found.run_id, turn.client_turn_id)
                if cached is not None:  # paso 1: un duplicado devuelve lo guardado aunque el run ya cerró
                    return cached
                try:
                    uow.acquire_turn(found.run_id, turn_id, self._clock.now(), self._config.lease_ttl)
                except TurnInProgress as exc:
                    raise EngineError(ProblemCode.turn_in_progress, "turno en curso") from exc
                leased = found.run_id
                cached = uow.get_turn_result(found.run_id, turn.client_turn_id)
                if cached is not None:  # el otro turno commiteó entre mi lectura y mi lease
                    uow.release_turn(found.run_id, turn_id)
                    uow.commit()
                    leased = None
                    return cached
                state = uow.load_run(found.run_id) or found  # estado fresco tras tomar el lease
                if state.status != "open":
                    raise EngineError(ProblemCode.run_closed, "run cerrado")
                outcome = self._process(uow, state, principal, on_behalf_of, turn, turn_id, meter)
                uow.commit()
                leased = None
                if isinstance(outcome, EngineError):
                    raise outcome
                return outcome
        except Exception:
            if leased is not None:
                self._release_quietly(leased, turn_id)
            raise

    def _release_quietly(self, run_id: str, turn_id: str) -> None:
        """Una excepción (no una caída) libera el lease con una UoW propia para que el reintento no espere
        al TTL. Una caída real (`BaseException`) no pasa por aquí: el lease vence solo."""
        try:
            with self._uow_factory() as uow:
                uow.release_turn(run_id, turn_id)
                uow.commit()
        except Exception:
            pass

    def _process(
        self,
        uow: UnitOfWork,
        state: RunState,
        principal: Principal,
        on_behalf_of: OnBehalfOf | None,
        turn: TurnInput,
        turn_id: str,
        meter: StageMeter,
    ) -> TurnResult | EngineError:
        """Pasos 3 a 14. Devuelve un `EngineError` (ya commiteable) cuando el turno cierra el run sin
        procesar el mensaje (abandono, P2)."""
        state = begin_turn(state.model_copy(update={"turn_count": state.turn_count + 1}), self._clock)
        runtime = self._runtimes.open(state, principal, on_behalf_of)
        agent, release = runtime.step.agent, runtime.step.release
        frame = self._new_frame(
            uow, state, turn_id, "turn", turn.client_turn_id, agent, runtime, meter, release
        )
        frame.turn = turn
        frame.text_model = runtime.model_text(turn.text)  # C1: el texto crudo no sale de aquí
        frame.buffer.reserve_turn_started()
        if self._registry.release_status(state.release) == "revoked":  # paso 3
            request = EscalationRequest(
                reason_code="release_revoked", target_queue=agent.default_target_queue, priority="normal"
            )
            with frame.meter.stage("flow"):
                self._closer.escalate(frame, request, "revocation")
            return self._finish(frame, record=True)
        if self._expired(frame):  # paso 4 (P2): se cierra y el mensaje no se procesa
            return EngineError(ProblemCode.run_closed, "run vencido por inactividad")
        if self._recover(frame):  # paso 5
            return self._finish(frame, record=True)
        frame.state, events = self._actions.expire_tokens(frame.state, turn_id=turn_id)  # paso 6
        frame.buffer.add(*events)
        if self._guard(frame):  # paso 7: unsupported / tamaño → plantilla, sin Understand ni flow
            return self._finish(frame, record=True)
        understood = self._understand_step(frame)  # paso 8
        with frame.meter.stage("flow"):  # pasos 9 a 12
            self._decide_and_advance(frame, understood)
        return self._finish(frame, record=True)

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
            frame.resume = NO_RESUME
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
        )
        with frame.meter.stage("understand"):
            outcome = self._understand.run(request)
        frame.cost_usd += outcome.cost_usd
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
        return outcome

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

    # --- start_run ----------------------------------------------------------------------------------

    def start_run(
        self, principal: Principal, on_behalf_of: OnBehalfOf | None, run_input: RunInput
    ) -> RunResult:
        """Crea el run (release fijada), emite `run_started` y arranca `entry_flow`. En modo task avanza
        hasta un terminal. No toma lease (el `run_id` es nuevo) ni guarda `client_turn_id` (M9)."""
        meter = StageMeter(self._clock)
        release = self._registry.resolve_release(run_input.agent, principal)
        version = release.entities.get(EntityKind.agent, {}).get(run_input.agent.id)
        if version is None:
            raise EngineError(ProblemCode.not_found, "agente")
        agent_ref = EntityRef(id=run_input.agent.id, version=version)
        agent = self._registry.get(agent_ref, Agent)
        now = self._clock.now()
        conversational = agent.mode == "conversational"
        locale = run_input.lang if run_input.lang in agent.supported_locales else agent.default_locale
        slots = {
            name: Slot(value=value, status="claimed", source_turn=1)
            for name, value in (run_input.input or {}).items()
        }
        state = RunState(
            run_id=self._ids.new_id(IdKind.run),
            session_id=self._ids.new_id(IdKind.session) if conversational else None,
            release=release.id,
            agent=agent_ref,
            principal=principal,
            on_behalf_of=on_behalf_of,
            subject=run_input.subject,
            mode=agent.mode,
            locale=locale,
            created_at=now,
            last_activity_at=now,
            inactive_after=now + agent.inactivity_ttl if conversational else None,
            turn_count=1,
            slots=slots,
        )
        turn_id = self._ids.new_id(IdKind.turn)
        state = begin_turn(state, self._clock)
        runtime = self._runtimes.open(state, principal, on_behalf_of)
        with self._uow_factory() as uow:
            frame = self._new_frame(uow, state, turn_id, "start_run", None, agent, runtime, meter, release)
            reportable: frozenset[str] = (
                self._authz.reportable_attrs() if self._authz is not None else frozenset()
            )
            attrs = {k: v for k, v in sorted(principal.attrs.items()) if k in reportable}
            frame.buffer.add(
                self._events.run_started(state, agent_ref, attrs),
                self._events.turn_started(state, turn_id, None, guards=None),
            )
            frame.state = start_flow(state, self._flow(release, agent.entry_flow))
            self._advance(frame)
            result = self._finish(frame, record=conversational)
            uow.commit()
        saved = frame.state
        return RunResult(
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
