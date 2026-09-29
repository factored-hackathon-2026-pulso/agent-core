"""Manejadores globales del turno como tabla de datos (m04 §3.2 y §9: agregar un comando = una fila)."""

from collections.abc import Callable
from dataclasses import dataclass

from agent_core.domain import (
    Command,
    EntityKind,
    EscalationRequest,
    Flow,
    Interrupt,
    InvalidationReason,
    JsonValue,
    Outcome,
    PendingIntent,
    Policy,
    StartFlowAction,
)
from agent_core.interpreter import NO_RESUME, Resume, evaluate, start_flow, truthy
from agent_core.ports import RegistryPort
from agent_core.turn.closing import Closer
from agent_core.turn.frame import TurnFrame
from agent_core.turn.intents import add_pending, clear_flow, offer_next
from agent_core.turn.ports import UnderstandOutcome
from agent_core.turn.refs import pinned_ref
from agent_core.turn.templates import render_engine

# Con un `confirm` pendiente solo estos tres manejadores conservan su efecto (m04 §3.2, ADR 0007).
PENDING_CONFIRM_KEEPS = frozenset({"interrupt", "cancel", "handoff"})


@dataclass(frozen=True)
class Handled:
    """Qué hace el motor tras un manejador: `ended` = el turno acaba sin avanzar el flow; `advance` = hay un
    flow nuevo y se avanza directo (sin elegir flow por Understand)."""

    ended: bool = False
    advance: bool = False


@dataclass(frozen=True)
class Signals:
    """Entrada de los manejadores: la salida de Understand y la interrupción que disparó (si alguna)."""

    outcome: UnderstandOutcome
    hit: Interrupt | None = None

    def below(self, field: str) -> bool:
        """Campo calibrado bajo el umbral (o sin marca: en la duda, no se confía)."""
        return not self.outcome.above_threshold.get(field, False)

    def is_(self, command: Command) -> bool:
        return self.outcome.command is command and not self.below("command")


@dataclass(frozen=True)
class Env:
    closer: Closer
    registry: RegistryPort


HandlerFn = Callable[[Env, TurnFrame, Signals], Handled]


@dataclass(frozen=True)
class GlobalHandler:
    name: str
    applies: Callable[[Signals], bool]
    run: HandlerFn


def find_interrupt(env: Env, frame: TurnFrame, outcome: UnderstandOutcome) -> Interrupt | None:
    """Interrupción por `priority` desc: el comando `interrupt` sobre umbral que la nombra, o su
    `signal_policy` evaluada sobre `{"message": {"text": <vista model>}}` (C3)."""
    signals = Signals(outcome)
    data: JsonValue = {"message": {"text": frame.text_model}}
    for interrupt in sorted(frame.release.interrupts, key=lambda i: -i.priority):
        named = (
            signals.is_(Command.interrupt)
            and not signals.below("interrupt")
            and outcome.interrupt == interrupt.id
        )
        if named:
            return interrupt
        if interrupt.signal_policy is not None:
            ref = pinned_ref(frame.release, EntityKind.policy, interrupt.signal_policy)
            policy = env.registry.get(ref, Policy)
            if truthy(evaluate(policy.expr, data)):
                return interrupt
    return None


# --- acciones ---------------------------------------------------------------------------------------


def _run_interrupt(env: Env, frame: TurnFrame, signals: Signals) -> Handled:
    interrupt = signals.hit
    assert interrupt is not None
    env.closer.invalidate(frame, InvalidationReason.interrupt)
    action = interrupt.action
    if not isinstance(action, StartFlowAction):
        request = EscalationRequest(
            reason_code=f"interrupt:{interrupt.id}",
            target_queue=action.target_queue,
            priority=action.priority,
        )
        env.closer.escalate(frame, request)
        return Handled(ended=True)
    flow = env.registry.get(pinned_ref(frame.release, EntityKind.flow, action.flow), Flow)
    state = frame.state
    if (
        state.active_flow is not None
    ):  # C7: el flow interrumpido se ofrece después y reinicia desde su entrada
        interrupted = env.registry.get(state.active_flow.flow, Flow)
        state = add_pending(
            state.model_copy(update={"active_flow": None}),
            [PendingIntent(flow=interrupted.id, priority=interrupted.priority, mention_order=0)],
        )
    frame.state = start_flow(state.model_copy(update={"pending_offer": None}), flow)
    frame.resume = NO_RESUME
    return Handled(advance=True)


def _run_cancel(env: Env, frame: TurnFrame, signals: Signals) -> Handled:
    """C8: cierra solo el flow (run abierto, sin mensaje). Si quedan pendientes, se ofrece la primera."""
    env.closer.invalidate(frame, InvalidationReason.cancel)
    frame.state = clear_flow(frame.state)
    frame.confirmation = None
    offer_next(frame, env.registry)
    return Handled(ended=True)


def _run_handoff(env: Env, frame: TurnFrame, signals: Signals) -> Handled:
    request = EscalationRequest(
        reason_code="customer_request", target_queue=frame.agent.default_target_queue, priority="normal"
    )
    env.closer.escalate(frame, request)
    return Handled(ended=True)


def _run_abstain(env: Env, frame: TurnFrame, signals: Signals) -> Handled:
    env.closer.invalidate(frame, InvalidationReason.cancel)
    frame.messages.append(
        render_engine(env.registry, frame.release, frame.agent.templates.abstain, frame.state.locale)
    )
    env.closer.close_run(frame, Outcome.abstained, "flow")
    return Handled(ended=True)


def _clarify_applies(signals: Signals) -> bool:
    outcome = signals.outcome
    return (
        outcome.command is Command.clarify
        or signals.below("command")
        or (outcome.command is Command.start_flow and signals.below("flow"))
        or (outcome.command is Command.interrupt and signals.below("interrupt"))
    )


def _run_clarify(env: Env, frame: TurnFrame, signals: Signals) -> Handled:
    """Aclaración; al superar `max_clarifications`: `end(clarify_exhausted)` o `escalate(low_confidence)`."""
    state, agent = frame.state, frame.agent
    if state.clarifications_used >= agent.max_clarifications:
        if agent.on_clarify_exhausted == "escalate":
            request = EscalationRequest(
                reason_code="low_confidence", target_queue=agent.default_target_queue, priority="normal"
            )
            env.closer.escalate(frame, request)
        else:  # el mensaje de cierre lo decide la app según `outcome` (no hay plantilla del motor)
            env.closer.invalidate(frame, InvalidationReason.cancel)
            env.closer.close_run(frame, Outcome.clarify_exhausted, "flow")
        return Handled(ended=True)
    frame.messages.append(render_engine(env.registry, frame.release, agent.templates.clarify, state.locale))
    frame.state = state.model_copy(
        update={
            "clarifications_used": state.clarifications_used + 1,
            "repair_turns_used": state.repair_turns_used + 1,
        }
    )
    return Handled(ended=True)


GLOBAL_HANDLERS: tuple[GlobalHandler, ...] = (
    GlobalHandler("interrupt", lambda s: s.hit is not None, _run_interrupt),  # 1
    GlobalHandler("cancel", lambda s: s.is_(Command.cancel), _run_cancel),  # 2
    GlobalHandler("handoff", lambda s: s.is_(Command.handoff), _run_handoff),  # 3
    GlobalHandler("out_of_scope", lambda s: s.is_(Command.out_of_scope), _run_abstain),  # 4
    GlobalHandler("clarify", _clarify_applies, _run_clarify),  # 5
)


def run_global_handlers(
    env: Env, frame: TurnFrame, outcome: UnderstandOutcome, *, confirm_pending: bool
) -> Handled | None:
    """Gana el primer manejador que aplique. Con `confirm` pendiente solo cuentan `PENDING_CONFIRM_KEEPS`.

    `None` = ninguno aplica: sigue la elección de flow (3.3) o la resolución del `confirm`."""
    signals = Signals(outcome, find_interrupt(env, frame, outcome))
    for handler in GLOBAL_HANDLERS:
        if confirm_pending and handler.name not in PENDING_CONFIRM_KEEPS:
            continue
        if handler.applies(signals):
            return handler.run(env, frame, signals)
    return None


def resolve_pending_confirm(outcome: UnderstandOutcome) -> Resume:
    """Con `confirm` pendiente y ningún manejador global: `affirm`/`deny` sobre umbral responden el
    confirm; todo lo demás (intención nueva, `out_of_scope`, `clarify`, bajo umbral) es `unclear`."""
    signals = Signals(outcome)
    if signals.is_(Command.affirm):
        return Resume("confirm_answer", "yes")
    if signals.is_(Command.deny):
        return Resume("confirm_answer", "no")
    return Resume("confirm_answer", "unclear")
