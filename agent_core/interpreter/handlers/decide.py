"""`decide` (M2 §3.3): entrada en vista `model`, umbral decidido por M5, rama = valor o `low_confidence`."""

from agent_core.domain import DecideNode, DecisionModelDef, EntityKind, RunState
from agent_core.interpreter.budgets import charge_model, model_budget_exhausted
from agent_core.interpreter.context import Resume, StepContext
from agent_core.interpreter.handlers.base import NodeResult, escalate_now
from agent_core.interpreter.projection import model_inputs
from agent_core.interpreter.refs import exact_ref
from agent_core.interpreter.resolve import MissingPath

LOW_CONFIDENCE = "low_confidence"


def handle_decide(node: DecideNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    if model_budget_exhausted(state, ctx):
        return escalate_now(state, ctx, "budget_exceeded")
    ref = exact_ref(ctx, EntityKind.decision_model, cfg.model)
    model = ctx.registry.get(ref, DecisionModelDef)
    paths = cfg.input_view if cfg.input_view is not None else model.input_view
    try:
        inputs = model_inputs(paths, state, ctx)
    except MissingPath:
        return NodeResult(state, result_key=LOW_CONFIDENCE)
    result = ctx.decisions.decide(ref, inputs, ctx.locale)
    state = charge_model(state, calls=result.model_calls, tokens=result.tokens, cost=result.cost_usd)
    state = state.model_copy(update={"decisions": {**state.decisions, cfg.save_as: result.decision}})
    value = result.decision.value.get(cfg.branch_on)
    calibrated = cfg.branch_on in model.calibrated_fields
    above = result.above_threshold.get(cfg.branch_on, not calibrated)
    key = value if above and isinstance(value, str) else LOW_CONFIDENCE
    return NodeResult(state, result_key=key, events=list(result.events))
