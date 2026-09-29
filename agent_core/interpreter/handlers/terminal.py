"""`escalate` y `end` (M2 §3.3): terminales."""

from agent_core.domain import EndNode, EscalateNode, JsonValue, RunState
from agent_core.interpreter.context import Resume, StepContext, Stop
from agent_core.interpreter.handlers.base import (
    DEFAULT_PRIORITY,
    NodeResult,
    escalate_now,
    escalation_request,
    rule_data,
)
from agent_core.interpreter.jsonlogic import evaluate
from agent_core.interpreter.resolve import MissingPath, resolve_value


def handle_escalate(node: EscalateNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    priority = DEFAULT_PRIORITY
    if cfg.priority_expr is not None:
        value = evaluate(cfg.priority_expr, rule_data(state))
        if isinstance(value, str) and value:
            priority = value
    request = escalation_request(ctx, cfg.reason_code, cfg.target_queue, priority)
    return NodeResult(state, stop=Stop.terminal, escalation=request)


def handle_end(node: EndNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    output: dict[str, JsonValue] | None = None
    if cfg.output_map and state.mode == "task":
        try:
            output = {key: resolve_value(state, path) for key, path in cfg.output_map.items()}
        except MissingPath:
            return escalate_now(state, ctx, "validation_failed")
    return NodeResult(state, stop=Stop.terminal, end_outcome=cfg.outcome, output=output)
