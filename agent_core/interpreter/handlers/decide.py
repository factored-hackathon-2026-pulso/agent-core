"""`decide` (M2 §3.3): entrada en vista `model`, umbral decidido por M5, rama = valor o `low_confidence`."""

from agent_core.domain import DecideNode, DecisionModelDef, EntityKind, RunState
from agent_core.interpreter.budgets import charge_model, model_budget_exhausted
from agent_core.interpreter.context import Resume, StepContext
from agent_core.interpreter.handlers.base import NodeResult, escalate_now
from agent_core.interpreter.projection import full_inputs, model_inputs
from agent_core.interpreter.refs import exact_ref
from agent_core.interpreter.resolve import MissingPath

LOW_CONFIDENCE = "low_confidence"


def handle_decide(node: DecideNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    if model_budget_exhausted(state, ctx):
        return escalate_now(state, ctx, "budget_exceeded")
    if cfg.choices_from is not None:
        return _decide_choice(node, state, ctx)
    ref = exact_ref(ctx, EntityKind.decision_model, cfg.model)
    model = ctx.registry.get(ref, DecisionModelDef)
    paths = cfg.input_view if cfg.input_view is not None else model.input_view
    try:
        inputs = model_inputs(paths, state, ctx)
    except MissingPath:
        return NodeResult(state, result_key=LOW_CONFIDENCE)
    if any(spec.compares_on_full_view for spec in model.providers):  # ADR 0027: solo ese modelo la pide
        try:
            full = full_inputs(paths, state)
        except MissingPath:
            return NodeResult(state, result_key=LOW_CONFIDENCE)
        result = ctx.decisions.decide(ref, inputs, ctx.locale, inputs_full=full)
    else:
        result = ctx.decisions.decide(ref, inputs, ctx.locale)
    state = charge_model(state, calls=result.model_calls, tokens=result.tokens, cost=result.cost_usd)
    state = state.model_copy(update={"decisions": {**state.decisions, cfg.save_as: result.decision}})
    value = result.decision.value.get(cfg.branch_on)
    calibrated = cfg.branch_on in model.calibrated_fields
    above = result.above_threshold.get(cfg.branch_on, not calibrated)
    key = value if above and isinstance(value, str) else LOW_CONFIDENCE
    return NodeResult(state, result_key=key, events=list(result.events))


NONE_CHOICE = "none"


def _choices(path: str, state: RunState, ctx: StepContext) -> list[str] | None:
    """The runtime options at `path` (a list of strings, de-duplicated preserving order), or None."""
    try:
        value = model_inputs([path], state, ctx)[path]
    except MissingPath:
        return None
    if not isinstance(value, list):
        return None
    names = [v for v in value if isinstance(v, str)]
    if len(names) != len(value):
        return None
    return list(dict.fromkeys(names))


def _decide_choice(node: DecideNode, state: RunState, ctx: StepContext) -> NodeResult:
    """`decide` with `choices_from` (ADR 0021): the model picks one runtime option or "none"."""
    cfg = node.config
    assert cfg.choices_from is not None
    choices = _choices(cfg.choices_from, state, ctx)
    if not choices:
        return NodeResult(state, result_key=NONE_CHOICE)
    if NONE_CHOICE in choices:
        # The schema adds "none" itself: a real option with that name would be ambiguous.
        return NodeResult(state, result_key=LOW_CONFIDENCE)
    try:
        inputs = model_inputs(cfg.input_view or [], state, ctx)
    except MissingPath:
        return NodeResult(state, result_key=LOW_CONFIDENCE)
    ref = exact_ref(ctx, EntityKind.decision_model, cfg.model)
    result = ctx.decisions.decide_choice(ref, inputs, choices, ctx.locale)
    state = charge_model(state, calls=result.model_calls, tokens=result.tokens, cost=result.cost_usd)
    state = state.model_copy(update={"decisions": {**state.decisions, cfg.save_as: result.decision}})
    value = result.decision.value.get("choice")
    if value == NONE_CHOICE:
        key = NONE_CHOICE
    elif isinstance(value, str) and value in choices and result.above_threshold.get("choice", False):
        key = "chosen"
    else:
        key = LOW_CONFIDENCE
    return NodeResult(state, result_key=key, events=list(result.events))
