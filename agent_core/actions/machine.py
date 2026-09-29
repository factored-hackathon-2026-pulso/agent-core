"""Máquina de estados de `Action` como dato (M3 §3.1). Cualquier otra transición es un bug, no un error de
usuario: `next_state` lanza `IllegalTransition`."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from agent_core.domain import ActionState, IllegalTransition


class Trigger(StrEnum):
    """Lo que mueve una acción de un estado a otro (M3 §3.1)."""

    confirm = "confirm"
    cancel = "cancel"
    dispatch = "dispatch"
    tool_ok = "tool_ok"
    tool_uncertain = "tool_uncertain"
    tool_denied = "tool_denied"
    tool_step_up = "tool_step_up"
    verified = "verified"
    failed = "failed"


TRANSITIONS: Mapping[tuple[ActionState, Trigger], ActionState] = MappingProxyType(
    {
        (ActionState.proposed, Trigger.confirm): ActionState.confirmed,
        (ActionState.proposed, Trigger.cancel): ActionState.cancelled,
        (ActionState.confirmed, Trigger.dispatch): ActionState.executing,
        (ActionState.confirmed, Trigger.cancel): ActionState.cancelled,
        (ActionState.executing, Trigger.tool_ok): ActionState.executed,
        (ActionState.executing, Trigger.tool_uncertain): ActionState.uncertain,
        (ActionState.executing, Trigger.tool_denied): ActionState.denied,
        # step_up_required llega antes de cualquier efecto: la acción vuelve a esperar (M3 §3.4).
        (ActionState.executing, Trigger.tool_step_up): ActionState.confirmed,
        # Recuperación: una acción que quedó en executing al cargar va a verify sin re-ejecutar (M3 §3.6).
        (ActionState.executing, Trigger.verified): ActionState.verified,
        (ActionState.executing, Trigger.failed): ActionState.failed,
        (ActionState.executed, Trigger.verified): ActionState.verified,
        (ActionState.executed, Trigger.failed): ActionState.failed,
        (ActionState.uncertain, Trigger.verified): ActionState.verified,
        (ActionState.uncertain, Trigger.failed): ActionState.failed,
    }
)

# `denied` es terminal y nunca pasa por verify (M0 rev. 2).
VERIFIABLE: frozenset[ActionState] = frozenset({ActionState.executed, ActionState.uncertain,
                                                ActionState.executing})


def next_state(current: ActionState, trigger: Trigger) -> ActionState:
    """Estado siguiente según la tabla; un par que no está en ella lanza `IllegalTransition`."""
    try:
        return TRANSITIONS[(current, trigger)]
    except KeyError:
        raise IllegalTransition(f"acción {current.value} --{trigger.value}--> no permitida") from None
