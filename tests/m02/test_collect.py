from decimal import Decimal

import pytest

from agent_core.domain import SlotValidator
from agent_core.interpreter import Resume, Stop
from agent_core.interpreter.handlers.collect import validate_slot
from tests.m02.harness import World, flow, template

TAIL = [
    {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "esc", "type": "escalate", "config": {"reason_code": "low_confidence"}},
]


def _collect(**over: object) -> dict[str, object]:
    config = {"slot": "codigo", "prompt_ref": "t/pedir@1.0.0", "max_attempts": 2,
              "validator": {"kind": "regex", "value": "[0-9]{4}"}, **over}
    return {"id": "c", "type": "collect", "config": config, "next": {"ok": "fin", "max_attempts": "esc"}}


def _world() -> World:
    w = World()
    w.add(template("t/pedir", "Dime el código"))
    return w


def _answer(text: object) -> Resume:
    return Resume("slot_answer", text)


def test_first_entry_prompts_and_waits() -> None:
    w = _world()
    out = w.step(w.state(flow(_collect(), *TAIL)))
    assert out.stop is Stop.awaiting_slot and [m.text for m in out.messages] == ["Dime el código"]
    assert out.state.active_flow.node_id == "c"  # type: ignore[union-attr]


def test_valid_answer_stores_validated_slot_and_continues() -> None:
    w = _world()
    state = w.step(w.state(flow(_collect(), *TAIL))).state
    out = w.step(state, _answer(" 1234 "))
    slot = out.state.slots["codigo"]
    assert (slot.value, slot.status, slot.source_turn) == ("1234", "validated", out.state.turn_count)
    assert out.stop is Stop.terminal and out.end_outcome is not None
    assert "c" not in out.state.node_attempts


def test_t_m2_05_reprompts_then_exits_by_max_attempts() -> None:
    w = _world()
    state = w.step(w.state(flow(_collect(), *TAIL))).state
    retry = w.step(state, _answer("abc"))
    assert retry.stop is Stop.awaiting_slot and [m.text for m in retry.messages] == ["Dime el código"]
    assert (retry.state.node_attempts["c"], retry.state.repair_turns_used) == (1, 1)
    assert "codigo" not in retry.state.slots
    exhausted = w.step(retry.state, _answer("xyz"))
    assert exhausted.escalation is not None and exhausted.escalation.reason_code == "low_confidence"
    assert ("c" not in exhausted.state.node_attempts, exhausted.state.repair_turns_used) == (True, 2)
    assert exhausted.state.active_flow.node_id == "esc"  # type: ignore[union-attr]


def test_non_text_answer_is_invalid() -> None:
    w = _world()
    state = w.step(w.state(flow(_collect(), *TAIL))).state
    assert w.step(state, _answer(None)).stop is Stop.awaiting_slot


@pytest.mark.parametrize(("validator", "raw", "ok", "value"), [
    (None, "  hola ", True, "hola"),
    (None, "   ", False, None),
    (SlotValidator(kind="type", value="string"), "x", True, "x"),
    (SlotValidator(kind="type", value="integer"), "42", True, 42),
    (SlotValidator(kind="type", value="integer"), "4.2", False, None),
    (SlotValidator(kind="type", value="decimal"), "12.50", True, Decimal("12.50")),
    (SlotValidator(kind="type", value="decimal"), "12,50", False, None),
    (SlotValidator(kind="regex", value="[A-Z]{2}[0-9]+"), "AB12", True, "AB12"),
    (SlotValidator(kind="regex", value="[A-Z]{2}[0-9]+"), "AB12x", False, None),  # fullmatch
    (SlotValidator(kind="enum", value=["Débito", "Crédito"]), "débito", True, "Débito"),
    (SlotValidator(kind="enum", value=["Débito", "Crédito"]), "otra", False, None),
])
def test_validate_slot(validator: SlotValidator | None, raw: object, ok: bool, value: object) -> None:
    assert validate_slot(validator, raw) == (ok, value)  # type: ignore[arg-type]


def test_decide_validator_is_not_supported_yet() -> None:
    with pytest.raises(NotImplementedError):
        validate_slot(SlotValidator(kind="decide", value="m@1.0.0"), "texto")


def test_max_attempts_exit_clears_attempts_so_a_loop_back_starts_from_zero() -> None:
    w = _world()
    node = _collect()
    node["next"] = {"ok": "fin", "max_attempts": "c"}  # vuelve al mismo collect
    state = w.step(w.state(flow(node, *TAIL))).state
    state = w.step(state, _answer("abc")).state
    again = w.step(state, _answer("xyz"))
    assert again.stop is Stop.awaiting_slot and "c" not in again.state.node_attempts
    retry = w.step(again.state, _answer("bad"))
    assert retry.state.node_attempts["c"] == 1
