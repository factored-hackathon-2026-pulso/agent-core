"""`transfer` (ADR 0021): builds the request; M4 validates it and either transfers or follows `rejected`."""

from agent_core.domain import DirectorySnapshot, JsonValue, RunState, TransferNode
from agent_core.interpreter.context import Resume, StepContext, Stop, TransferRequest
from agent_core.interpreter.handlers.base import NodeResult


def _target(node: TransferNode, state: RunState) -> str | None:
    decision = state.decisions.get(node.config.target_from.split(".")[1])
    value = decision.value.get("choice") if decision is not None else None
    return value if isinstance(value, str) and value != "none" else None


def _snapshot(node: TransferNode, state: RunState) -> DirectorySnapshot | None:
    fact = state.facts.get(node.config.directory_from)
    if fact is None or not isinstance(fact.value, dict):
        return None
    try:
        return DirectorySnapshot.model_validate({k: v for k, v in fact.value.items() if k != "choices"})
    except ValueError:
        return None


def handle_transfer(node: TransferNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    """Stops terminal without a `result_key`: the pointer stays on this node so M4 can follow `rejected`."""
    slots: dict[str, JsonValue] = {
        name: state.slots[name].value for name in node.config.packet.slots
        if name in state.slots and state.slots[name].status == "validated"}
    request = TransferRequest(node_id=node.id, target=_target(node, state), snapshot=_snapshot(node, state),
                              reason=node.config.packet.reason, slots=slots)
    return NodeResult(state, stop=Stop.terminal, transfer=request)
