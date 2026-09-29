from decimal import Decimal

import pytest

from agent_core.domain import Decision
from agent_core.flows import parse_path
from agent_core.interpreter.resolve import (
    MissingPath,
    input_ids,
    render_template,
    resolve_args,
    resolve_path,
)
from testing.builders import run_state
from tests.m02.harness import fact, slot


def _state():  # type: ignore[no-untyped-def]
    return run_state(
        slots={"s": slot("hola"), "c": slot("x", "claimed")},
        facts={"tx": fact({"amount": Decimal("9.50"), "cur": "USD"}, fact_id="fact-0007")},
        decisions={"d": Decision(decision_id="decision-0009", value={"match": "unica", "p": {"a": 1}},
                                 p_cal={"match": 0.9}, provider_used="s", model_version="1")},
    )


def _resolve(text: str):  # type: ignore[no-untyped-def]
    path = parse_path(text)
    assert path is not None
    return resolve_path(_state(), path)


def test_resolves_slots_facts_and_decisions() -> None:
    assert _resolve("slots.s") == "hola"
    assert _resolve("facts.tx.value.amount") == Decimal("9.50")
    assert _resolve("facts.tx.value") == {"amount": Decimal("9.50"), "cur": "USD"}
    assert _resolve("decisions.d.match") == "unica"
    assert _resolve("decisions.d.p.a") == 1


@pytest.mark.parametrize("text", [
    "slots.c",  # D7: un slot claimed cuenta como ausente
    "slots.nope", "facts.nope.value", "facts.tx.value.nope", "decisions.d.nope", "decisions.nope.match",
    "facts.tx",  # el hecho completo no es una ruta de valor
])
def test_missing_paths_raise(text: str) -> None:
    with pytest.raises(MissingPath):
        _resolve(text)


def test_resolve_args_is_recursive_and_keeps_literals() -> None:
    args = {"a": "slots.s", "n": {"x": ["facts.tx.value.cur", "USD", 3]}, "lit": "texto libre"}
    assert resolve_args(_state(), args) == {"a": "hola", "n": {"x": ["USD", "USD", 3]}, "lit": "texto libre"}


def test_resolve_args_malformed_or_missing_path_is_missing() -> None:
    with pytest.raises(MissingPath):
        resolve_args(_state(), {"a": "slots.Mal"})
    with pytest.raises(MissingPath):
        resolve_args(_state(), {"a": "facts.nope.value"})


def test_input_ids_in_order_without_repeats() -> None:
    args = {"m": "facts.tx.value.amount", "c": "facts.tx.value.cur", "t": "decisions.d.match", "s": "slots.s"}
    assert input_ids(_state(), args) == ["fact-0007", "decision-0009"]


def test_render_template_formats_values() -> None:
    values = {"facts.a.value.x": Decimal("1E+2"), "slots.b": "hola", "facts.a.value.n": 3,
              "facts.a.value.z": None, "facts.a.value.t": True}
    text = (
        "{{ facts.a.value.x }}|{{slots.b}}|{{ facts.a.value.n }}|"
        "[{{ facts.a.value.z }}]|{{ facts.a.value.t }}"
    )
    assert render_template(text, values) == "100|hola|3|[]|true"
