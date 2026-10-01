"""Paso 5: recuperación de acciones en `executing` (ADR 0007 §4, M3 §3.6).

Una acción que quedó en `executing` tras una caída nunca se re-ejecuta: el flow se posiciona en el nodo que
sigue a la escritura por la rama `uncertain` (el `verify`) y M2 avanza desde ahí antes del mensaje."""

from collections.abc import Sequence

from agent_core.domain import Action, Flow, IllegalTransition, RunState, WriteToolNode, node_kind


def _writes_action(node: WriteToolNode, action: Action) -> bool:
    """El nodo de escritura de una acción: por `write_node_id` si es draft, por `action_from` con confirm."""
    if action.write_node_id is not None:
        return node.config.draft and node.id == action.write_node_id
    return node.config.action_from is not None and node.config.action_from == action.confirm_node_id


def position_at_verify(state: RunState, action_ids: Sequence[str], flow: Flow) -> RunState:
    """Mueve `active_flow.node_id` al destino `uncertain` del `tool` de escritura de la primera acción."""
    if state.active_flow is None:
        raise IllegalTransition(f"{state.run_id}: acción en executing sin flow activo")
    by_id = {a.action_id: a for a in state.actions}
    action = by_id[action_ids[0]]
    for node in flow.nodes:
        if node_kind(node) == "tool_write":
            assert isinstance(node, WriteToolNode)
            if _writes_action(node, action):
                target = node.next.get("uncertain") or node.next.get("ok")
                if target is None:
                    break
                active = state.active_flow.model_copy(update={"node_id": target})
                return state.model_copy(update={"active_flow": active})
    raise LookupError(f"{flow.id}: no hay nodo de escritura para la acción {action.action_id}")
