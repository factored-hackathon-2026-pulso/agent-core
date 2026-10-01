"""Confirmación acotada e idempotente (M3 §3.2, §3.3; ADR 0007 §8).

Solo se guarda el hash del token. Por eso la reentrada con el token vigente conserva `action_id` y
`token_exp`, pero rota el token (M3 rev. 2): el anterior deja de confirmar.
"""

import hmac
from copy import deepcopy
from datetime import datetime

from agent_core.actions.context import ActionContext
from agent_core.actions.events import ConfirmSource, EventFactory
from agent_core.actions.machine import Trigger
from agent_core.actions.results import Answer, AnswerResult
from agent_core.actions.store import add_action, move, proposed_for, replace_action
from agent_core.domain import (
    Action,
    ActionCancelled,
    ActionState,
    ConfirmationPrompt,
    ConfirmNode,
    EngineEvent,
    EntityRef,
    IllegalTransition,
    InvalidationReason,
    JsonValue,
    Message,
    RunState,
    ToolDef,
    canonical_bytes,
    sha256_hex,
)
from agent_core.ports import Clock, IdKind, IdSource


def token_hash(token: str) -> str:
    """Hash del token de confirmación: lo único que se guarda (M3 §3.2)."""
    return sha256_hex(token.encode("utf-8"))


def _expiry(action: Action) -> datetime:
    """El vencimiento de una acción con confirm; una escritura draft no se confirma (ADR 0019)."""
    if action.token_exp is None:
        raise IllegalTransition(f"la acción {action.action_id} no se confirma: es una escritura draft")
    return action.token_exp


def _digest(action: Action) -> str:
    if action.confirmation_token_hash is None:
        raise IllegalTransition(f"la acción {action.action_id} no se confirma: es una escritura draft")
    return action.confirmation_token_hash


def _prompt(action: Action, token: str, summary: Message) -> ConfirmationPrompt:
    return ConfirmationPrompt(action_id=action.action_id, token=token, expires_at=_expiry(action),
                              summary=summary)


class Confirmations:
    """`propose`, `answer`, `expire_tokens` e `invalidate`: funciones puras sobre `RunState` más eventos."""

    def __init__(self, ids: IdSource, clock: Clock, events: EventFactory) -> None:
        self._ids = ids
        self._clock = clock
        self._events = events

    def propose(
        self, state: RunState, node: ConfirmNode, resolved_args: dict[str, JsonValue], tool_def: ToolDef,
        ctx: ActionContext,
    ) -> tuple[RunState, ConfirmationPrompt, list[EngineEvent]]:
        if state.active_flow is None:
            raise IllegalTransition(f"confirm {node.id}: no hay flow activo")
        if tool_def.id != node.config.action.tool.id or not tool_def.is_write:
            raise ValueError(f"confirm {node.id}: tool_def no es la escritura que declara el nodo")
        flow = state.active_flow.flow
        now = self._clock.now()
        events: list[EngineEvent] = []
        current = proposed_for(state, node.id)
        args = deepcopy(resolved_args)  # congelada: nadie comparte estado mutable con el llamador
        args_hash = sha256_hex(canonical_bytes(args))
        tool_ref = EntityRef(id=tool_def.id, version=tool_def.version)
        if current is not None and now < _expiry(current) and (current.args_hash != args_hash
                                                                or current.tool != tool_ref):
            # Lo que el usuario vio ya no es lo que el flow propone: no se reutiliza (M3 §11 (a), (f)).
            state, cancelled = self._cancel(state, current, InvalidationReason.args_changed, ctx.turn_id)
            events.append(cancelled)
            current = None
        if current is not None and now < _expiry(current):
            token = self._ids.secret_token()
            rotated = current.model_copy(update={"confirmation_token_hash": token_hash(token)})
            state = replace_action(state, rotated)
            template = node.config.reprompt_template or node.config.summary_template
            return state, _prompt(rotated, token, ctx.render(template, state)), events
        if current is not None:
            state, cancelled = self._cancel(state, current, InvalidationReason.token_expired, ctx.turn_id)
            events.append(cancelled)
        action_id = self._ids.new_id(IdKind.action)
        token = self._ids.secret_token()
        action = Action(
            action_id=action_id,
            confirm_node_id=node.id,
            flow=flow,
            tool=tool_ref,
            args=args,
            args_hash=args_hash,
            state=ActionState.proposed,
            confirmation_token_hash=token_hash(token),
            token_exp=now + tool_def.confirmation_ttl,
            idempotency_key=action_id,
            created_at=now,
        )
        state = add_action(state, action)
        return state, _prompt(action, token, ctx.render(node.config.summary_template, state)), events

    def answer(self, state: RunState, node: ConfirmNode, reply: Answer, token: str | None,
               ctx: ActionContext) -> tuple[RunState, AnswerResult, list[EngineEvent]]:
        current = proposed_for(state, node.id)
        if current is None:
            raise IllegalTransition(f"confirm {node.id}: no hay acción proposed")
        if reply == "yes":
            if self._clock.now() >= _expiry(current):  # un token vencido nunca confirma
                state, event = self._cancel(state, current, InvalidationReason.token_expired, ctx.turn_id)
                return state, "unclear", [event]
            matches = token is None or hmac.compare_digest(token_hash(token), _digest(current))
            if not matches:
                return state, "unclear", []  # token de otra propuesta (p. ej. rotado): no confirma ni suma
            state = replace_action(state, move(current, Trigger.confirm))
            source: ConfirmSource = "button" if token is not None else "understand"
            return state, "yes", [self._events.confirmed(state, ctx.turn_id, current.action_id, source)]
        if reply == "no":
            state, event = self._cancel(state, current, InvalidationReason.denied_by_user, ctx.turn_id)
            return state, "no", [event]
        attempts = state.node_attempts.get(node.id, 0) + 1  # repair_turns_used lo suma M4
        state = state.model_copy(update={"node_attempts": {**state.node_attempts, node.id: attempts}})
        if attempts >= node.config.max_attempts:
            state, event = self._cancel(state, current, InvalidationReason.max_attempts, ctx.turn_id)
            return state, "max_attempts", [event]
        return state, "unclear", []

    def expire_tokens(self, state: RunState, turn_id: str | None) -> tuple[RunState, list[EngineEvent]]:
        now = self._clock.now()
        expired = [a for a in state.actions if a.state is ActionState.proposed and now >= _expiry(a)]
        return self._cancel_all(state, expired, InvalidationReason.token_expired, turn_id)

    def invalidate(self, state: RunState, reason: InvalidationReason,
                   turn_id: str | None) -> tuple[RunState, list[EngineEvent]]:
        pending = [a for a in state.actions if a.state in (ActionState.proposed, ActionState.confirmed)]
        return self._cancel_all(state, pending, reason, turn_id)

    def _cancel_all(self, state: RunState, actions: list[Action], reason: InvalidationReason,
                    turn_id: str | None) -> tuple[RunState, list[EngineEvent]]:
        events: list[EngineEvent] = []
        for action in actions:
            state, event = self._cancel(state, action, reason, turn_id)
            events.append(event)
        return state, events

    def _cancel(self, state: RunState, action: Action, reason: InvalidationReason,
                turn_id: str | None) -> tuple[RunState, ActionCancelled]:
        state = replace_action(state, move(action, Trigger.cancel, cancel_reason=reason))
        return state, self._events.cancelled(state, turn_id, action.action_id, reason)
