from decimal import Decimal
from typing import Any

import pytest

from agent_core.flows.jsonlogic import JSONLOGIC_OPS, expr_literals, expr_paths, jsonlogic_problems


def test_operator_list_is_closed() -> None:
    assert set(JSONLOGIC_OPS) == {
        "var",
        "==",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "and",
        "or",
        "!",
        "in",
        "if",
        "missing",
    }


@pytest.mark.parametrize(
    "expr",
    [
        {"==": [{"var": "readback.status"}, "Open"]},
        {"and": [{">": [{"var": "facts.m.value"}, {"var": "facts.n.value"}]}, {"!": {"var": "slots.x"}}]},
        {"if": [{"var": "slots.a"}, 1, {"var": "slots.b"}, 2, 3]},
        {"missing": ["slots.a", "facts.b.value"]},
        {"var": ["slots.a", "defecto"]},
        None,
    ],
)
def test_valid_expressions(expr: Any) -> None:
    assert jsonlogic_problems(expr) == []


@pytest.mark.parametrize(
    ("expr", "fragment"),
    [
        ({"+": [1, 2]}, "operador no permitido"),
        ({"==": [1]}, "aridad"),
        ({"if": [True, 1]}, "aridad"),
        ({"==": [1, 2], "!=": [1, 2]}, "exactamente una clave"),
        ({"var": "USD"}, "ruta inválida"),
        ({"var": "slots.a.b"}, "ruta inválida"),
        ({"var": 3}, "string"),
        ({"and": [{"merge": [1]}]}, "operador no permitido"),
    ],
)
def test_invalid_expressions(expr: Any, fragment: str) -> None:
    problems = jsonlogic_problems(expr)
    assert problems and fragment in problems[0]


def test_expr_paths_and_literals() -> None:
    expr = {
        "and": [
            {">": [{"var": "facts.m.value"}, Decimal("500")]},
            {"in": [{"var": ["slots.t", "x"]}, ["a", None]]},
            {"missing": ["slots.z"]},
        ]
    }
    assert [p.raw for p in expr_paths(expr)] == ["facts.m.value", "slots.t", "slots.z"]
    assert [value for _, value in expr_literals(expr)] == [Decimal("500"), "x", "a", None]
    pointers = [where for where, _ in expr_literals(expr)]
    assert pointers[0] == "/and/0/>/1"


def _deep(levels: int) -> Any:
    expr: Any = {"var": "slots.a"}
    for _ in range(levels):
        expr = {"!": expr}
    return expr


def test_deep_nesting_is_reported_not_raised() -> None:
    problems = jsonlogic_problems(_deep(50_000))
    assert problems and "profundidad" in problems[0]
    assert expr_paths(_deep(50_000)) == []
    assert list(expr_literals(_deep(50_000))) == []


def test_reasonable_depth_is_accepted() -> None:
    assert jsonlogic_problems(_deep(20)) == []
    assert [p.raw for p in expr_paths(_deep(20))] == ["slots.a"]


def _nested(levels: int) -> object:
    expr: object = {"var": "slots.a"}
    for _ in range(levels):
        expr = {"!": [expr]}
    return expr


def test_exceeds_max_depth_boundary_and_scalars() -> None:
    from agent_core.flows.jsonlogic import MAX_DEPTH, exceeds_max_depth, jsonlogic_problems

    ok, deep = _nested(MAX_DEPTH - 1), _nested(MAX_DEPTH)
    assert not exceeds_max_depth(ok)  # type: ignore[arg-type]
    assert exceeds_max_depth(deep)  # type: ignore[arg-type]
    assert not jsonlogic_problems(ok)  # type: ignore[arg-type]
    assert jsonlogic_problems(deep)  # type: ignore[arg-type]
    assert not exceeds_max_depth(5) and not exceeds_max_depth("x") and not exceeds_max_depth(None)
    assert exceeds_max_depth(_nested(5000))  # type: ignore[arg-type]  # sin recursión
