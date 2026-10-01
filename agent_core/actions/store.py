"""Lecturas y reemplazos de `RunState.actions`. M3 es su único escritor (índice §5)."""

from collections.abc import Collection

from agent_core.actions.machine import Trigger, next_state
from agent_core.domain import Action, ActionState, IllegalTransition, RunState


def move(action: Action, trigger: Trigger, **update: object) -> Action:
    """Aplica una transición de la tabla; una no permitida lanza `IllegalTransition`."""
    return action.model_copy(update={"state": next_state(action.state, trigger), **update})


def replace_action(state: RunState, action: Action) -> RunState:
    actions = [action if a.action_id == action.action_id else a for a in state.actions]
    return state.model_copy(update={"actions": actions})


def add_action(state: RunState, action: Action) -> RunState:
    return state.model_copy(update={"actions": [*state.actions, action]})


def proposed_for(state: RunState, confirm_node_id: str) -> Action | None:
    """La acción `proposed` de un `confirm` (a lo sumo una: invariante de M0 §2.6)."""
    return next((a for a in state.actions
                 if a.confirm_node_id == confirm_node_id and a.state is ActionState.proposed), None)


def single_action(state: RunState, states: Collection[ActionState], confirm_node_id: str | None = None,
                  write_node_id: str | None = None) -> Action:
    """La única acción del flow activo en `states` (y del `confirm` o del nodo draft dados).

    Ninguna o varias: bug del llamador."""
    if state.active_flow is None:
        raise IllegalTransition("no hay flow activo")
    flow = state.active_flow.flow
    found = [a for a in state.actions
             if a.flow == flow and a.state in states
             and (confirm_node_id is None or a.confirm_node_id == confirm_node_id)
             and (write_node_id is None or a.write_node_id == write_node_id)]
    if len(found) != 1:
        wanted = sorted(s.value for s in states)
        raise IllegalTransition(f"se esperaba una acción en {wanted}, hay {len(found)}")
    return found[0]
