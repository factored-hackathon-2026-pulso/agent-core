"""Registro de handlers por tipo de nodo (M2 §2). Agregar un tipo de nodo = agregar su handler aquí."""

from collections.abc import Mapping
from types import MappingProxyType

from agent_core.interpreter.handlers.agent import handle_agent
from agent_core.interpreter.handlers.base import NodeHandler, NodeResult
from agent_core.interpreter.handlers.collect import handle_collect
from agent_core.interpreter.handlers.decide import handle_decide
from agent_core.interpreter.handlers.knowledge import handle_knowledge
from agent_core.interpreter.handlers.respond import handle_respond
from agent_core.interpreter.handlers.rule import handle_rule
from agent_core.interpreter.handlers.terminal import handle_end, handle_escalate
from agent_core.interpreter.handlers.tool import handle_tool
from agent_core.interpreter.handlers.transfer import handle_transfer
from agent_core.interpreter.handlers.write import handle_confirm, handle_verify, handle_write

HANDLERS: Mapping[str, NodeHandler] = MappingProxyType({
    "collect": handle_collect,
    "respond": handle_respond,
    "decide": handle_decide,
    "rule": handle_rule,
    "escalate": handle_escalate,
    "end": handle_end,
    "tool": handle_tool,
    "confirm": handle_confirm,
    "tool_write": handle_write,
    "verify": handle_verify,
    "agent": handle_agent,
    "knowledge": handle_knowledge,
    "transfer": handle_transfer,
})

__all__ = ["HANDLERS", "NodeHandler", "NodeResult"]
