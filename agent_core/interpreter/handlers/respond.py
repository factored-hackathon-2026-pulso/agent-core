"""`respond` (M2 §3.3): `template_ref` o `generate` (M8), con modo degradado y `await`."""

from agent_core.domain import Flow, GenerateConfig, IllegalTransition, NodeId, RespondNode, RunState
from agent_core.flows import derive_claims, release_view
from agent_core.interpreter.budgets import charge_model, model_budget_exhausted
from agent_core.interpreter.context import Resume, StepContext, Stop
from agent_core.interpreter.handlers.base import NodeResult, escalate_now
from agent_core.interpreter.ports import GenerateRequest
from agent_core.interpreter.resolve import MissingPath
from agent_core.interpreter.templates import render_message


def _claims(state: RunState, ctx: StepContext, node_id: NodeId) -> frozenset[str]:
    assert state.active_flow is not None
    flow = ctx.registry.get(state.active_flow.flow, Flow)
    return derive_claims(flow, release_view(ctx.registry, ctx.release)).get(node_id, frozenset())


def _generate(node: RespondNode, config: GenerateConfig, state: RunState, ctx: StepContext) -> NodeResult:
    """Resultado del nodo: terminal (escalamiento) o con el mensaje ya entregado."""
    if ctx.degraded:  # sin llamar al modelo (T-M2-10)
        message = render_message(state, ctx, config.fallback_template_ref)
        return NodeResult(state, result_key="next", messages=[message])
    if model_budget_exhausted(state, ctx):
        return escalate_now(state, ctx, "budget_exceeded")
    request = GenerateRequest(node_id=node.id, config=config, claims=_claims(state, ctx, node.id))
    result = ctx.responder.generate(request, state)
    state = charge_model(state, calls=result.model_calls, tokens=result.tokens, cost=result.cost_usd)
    if result.escalation is not None:
        return NodeResult(state, stop=Stop.terminal, escalation=result.escalation,
                          events=list(result.events), rejected=list(result.rejected))
    if result.message is None:
        raise IllegalTransition("el responder no devolvió mensaje ni escalamiento")
    return NodeResult(state, result_key="next", messages=[result.message],
                      events=list(result.events), rejected=list(result.rejected))


def handle_respond(node: RespondNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    try:
        if cfg.template_ref is not None:
            message = render_message(state, ctx, cfg.template_ref)
            result = NodeResult(state, result_key="next", messages=[message])
        else:
            assert cfg.generate is not None
            result = _generate(node, cfg.generate, state, ctx)
    except MissingPath:
        return escalate_now(state, ctx, "validation_failed")
    if result.stop is None and cfg.await_:
        result.stop = Stop.awaiting_user
    return result
