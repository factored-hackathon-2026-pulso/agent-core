"""`collect` (M2 §3.3): pregunta, valida y reintenta hasta `max_attempts`. Los validadores: D14."""

import re
from decimal import Decimal

from agent_core.domain import CollectNode, JsonValue, RunState, Slot, SlotValidator
from agent_core.interpreter.context import Resume, StepContext, Stop
from agent_core.interpreter.handlers.base import NodeResult, clear_attempts, escalate_now
from agent_core.interpreter.resolve import MissingPath
from agent_core.interpreter.templates import render_message

_INTEGER = re.compile(r"[+-]?[0-9]+")
_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")


def _typed(kind: JsonValue, text: str) -> tuple[bool, JsonValue]:
    if kind == "string":
        return bool(text), text or None
    if kind == "integer":
        return (True, int(text)) if _INTEGER.fullmatch(text) else (False, None)
    if kind == "decimal":
        return (True, Decimal(text)) if _DECIMAL.fullmatch(text) else (False, None)
    raise ValueError(f"tipo de slot desconocido: {kind!r}")


def validate_slot(validator: SlotValidator | None, raw: JsonValue) -> tuple[bool, JsonValue]:
    """`(ok, valor)`; el valor ya viene normalizado (recortado, tipado o canónico del enum)."""
    if not isinstance(raw, str):
        return False, None
    text = raw.strip()
    if validator is None:
        return bool(text), text or None
    match validator.kind:
        case "type":
            return _typed(validator.value, text)
        case "regex":
            try:
                ok = re.fullmatch(str(validator.value), text) is not None
            except re.error:
                ok = False
            return ok, text if ok else None
        case "enum":
            allowed = validator.value if isinstance(validator.value, list) else []
            for option in allowed:
                if isinstance(option, str) and option.casefold() == text.casefold():
                    return True, option
            return False, None
        case _:
            raise NotImplementedError("validador de slot 'decide': pendiente (M2 §11)")


def handle_collect(node: CollectNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config

    def ask() -> NodeResult:
        try:
            prompt = render_message(state, ctx, cfg.prompt_ref)
        except MissingPath:
            return escalate_now(state, ctx, "validation_failed")
        return NodeResult(state, stop=Stop.awaiting_slot, messages=[prompt])

    if resume.kind != "slot_answer":
        return ask()
    ok, value = validate_slot(cfg.validator, resume.value)
    if ok:
        slot = Slot(value=value, status="validated", source_turn=state.turn_count)
        state = clear_attempts(state, node.id).model_copy(update={"slots": {**state.slots, cfg.slot: slot}})
        return NodeResult(state, result_key="ok")
    attempts = state.node_attempts.get(node.id, 0) + 1
    state = state.model_copy(update={
        "node_attempts": {**state.node_attempts, node.id: attempts},
        "repair_turns_used": state.repair_turns_used + 1,  # D15: M4 lo lee
    })
    if attempts >= cfg.max_attempts:
        return NodeResult(clear_attempts(state, node.id), result_key="max_attempts")
    return ask()
