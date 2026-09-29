"""`rule` (M2 §3.3, ADR 0009): JSON Logic sobre hechos `full` y slots `validated`, nunca `decisions`."""

from agent_core.domain import EntityKind, EntityRef, JsonValue, Policy, RuleNode, RunState
from agent_core.interpreter.audit import rule_inputs
from agent_core.interpreter.context import Resume, StepContext
from agent_core.interpreter.events import Events
from agent_core.interpreter.handlers.base import NodeResult, rule_data
from agent_core.interpreter.jsonlogic import evaluate, truthy
from agent_core.interpreter.refs import exact_ref


def _expression(node: RuleNode, ctx: StepContext) -> tuple[JsonValue, EntityRef | None]:
    if node.config.policy is None:
        return node.config.expr, None
    ref = exact_ref(ctx, EntityKind.policy, node.config.policy)
    return ctx.registry.get(ref, Policy).expr, ref


def handle_rule(node: RuleNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    expr, policy = _expression(node, ctx)
    reads: list[str] = []
    result = truthy(evaluate(expr, rule_data(state), reads))
    event = Events(ctx).rule_evaluated(state, node.id, policy, rule_inputs(state, ctx, reads), result)
    return NodeResult(state, result_key="true" if result else "false", events=[event])
