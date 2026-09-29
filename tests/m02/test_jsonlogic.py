from decimal import Decimal

import pytest

from agent_core.flows import JSONLOGIC_OPS
from agent_core.interpreter import evaluate, truthy
from agent_core.interpreter.jsonlogic import OPS


def test_implements_exactly_the_closed_operator_set() -> None:
    assert set(OPS) == set(JSONLOGIC_OPS)


def test_t_m2_12_decimal_comparison_is_exact() -> None:
    assert evaluate({">": [{"var": "x"}, 500]}, {"x": Decimal("500.00")}) is False
    assert evaluate({">=": [{"var": "x"}, 500]}, {"x": Decimal("500.00")}) is True
    assert evaluate({">": [{"var": "x"}, 500]}, {"x": Decimal("500.01")}) is True
    assert evaluate({"==": [{"var": "x"}, Decimal("0.3")]}, {"x": Decimal("0.30")}) is True


@pytest.mark.parametrize(("expr", "data", "expected"), [
    ({"==": [1, True]}, {}, False),  # bool no es número
    ({"==": ["1", 1]}, {}, False),  # sin coerción de tipos
    ({"!=": ["a", "b"]}, {}, True),
    ({"<": ["a", "b"]}, {}, True),
    ({"<": [1, "b"]}, {}, False),  # tipos incomparables → False, nunca excepción
    ({">": [{"var": "ausente"}, 500]}, {}, False),  # None > 500
    ({"and": [True, 1, "x"]}, {}, "x"),
    ({"and": [True, 0, "x"]}, {}, 0),
    ({"or": [0, "", "y"]}, {}, "y"),
    ({"!": [0]}, {}, True),
    ({"in": ["a", ["a", "b"]]}, {}, True),
    ({"in": ["ol", "hola"]}, {}, True),
    ({"in": [1, "hola"]}, {}, False),
    ({"if": [False, "a", True, "b", "c"]}, {}, "b"),
    ({"if": [False, "a", "c"]}, {}, "c"),
    ({"missing": ["a", "b"]}, {"a": 1}, ["b"]),
    ({"var": ["ausente", 7]}, {}, 7),
    ({"var": "a.b"}, {"a": {"b": 3}}, 3),
])
def test_operators(expr: object, data: object, expected: object) -> None:
    assert evaluate(expr, data) == expected  # type: ignore[arg-type]


def test_reads_trace_the_paths_actually_read() -> None:
    reads: list[str] = []
    evaluate({"and": [{"var": "a"}, {"missing": ["b"]}, {"var": "c"}]}, {"a": True}, reads)
    assert reads == ["a", "b", "c"]
    short: list[str] = []
    evaluate({"or": [{"var": "a"}, {"var": "c"}]}, {"a": True}, short)
    assert short == ["a"]  # cortocircuito: solo lo que se leyó


def test_unknown_operator_and_depth_are_errors() -> None:
    with pytest.raises(ValueError, match="operador"):
        evaluate({"+": [1, 2]}, {})
    deep: object = 1
    for _ in range(70):
        deep = {"!": [deep]}
    with pytest.raises(ValueError, match="profundidad"):
        evaluate(deep, {})  # type: ignore[arg-type]


def test_truthy() -> None:
    assert [truthy(v) for v in (None, False, 0, Decimal("0"), "", [], True, 1, "a", [0])] == [
        False, False, False, False, False, False, True, True, True, True]
