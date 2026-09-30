"""`check_output`: subconjunto cerrado de JSON Schema para la salida del nodo `agent` (m02 §3.7)."""

from decimal import Decimal
from typing import Any

import pytest

from agent_core.interpreter.schema import check_output

OBJ = {"type": "object", "properties": {"n": {"type": "integer"}, "tags": {"type": "array",
       "items": {"type": "string"}}, "kind": {"enum": ["a", "b"]}}, "required": ["n"],
       "additionalProperties": False}


@pytest.mark.parametrize("value", [{"n": 1}, {"n": 2, "tags": ["x"], "kind": "a"}])
def test_valid_outputs(value: Any) -> None:
    assert check_output(OBJ, value) is None


@pytest.mark.parametrize("value, fragment", [
    ({}, "falta la propiedad 'n'"),
    ({"n": "1"}, "/n: tipo"),
    ({"n": True}, "/n: tipo"),  # un booleano no es un entero
    ({"n": 1, "extra": 1}, "propiedad no permitida 'extra'"),
    ({"n": 1, "tags": ["x", 2]}, "/tags/1: tipo"),
    ({"n": 1, "kind": "z"}, "/kind: fuera de los valores permitidos"),
    ([], "tipo distinto de object"),
])
def test_invalid_outputs_name_the_path_and_never_the_value(value: Any, fragment: str) -> None:
    error = check_output(OBJ, value)
    assert error is not None and fragment in error


def test_the_error_never_repeats_the_value() -> None:
    error = check_output({"type": "object", "properties": {"n": {"type": "integer"}}}, {"n": "secreto-123"})
    assert error is not None and "secreto-123" not in error


def test_decimal_is_a_number_and_not_an_integer() -> None:
    assert check_output({"type": "number"}, Decimal("1.5")) is None
    assert check_output({"type": "integer"}, Decimal("1.5")) is not None


def test_annotations_are_ignored_but_unknown_keywords_fail_closed() -> None:
    assert check_output({"type": "string", "description": "texto"}, "x") is None
    error = check_output({"type": "string", "pattern": "^a"}, "b")
    assert error is not None and "pattern" in error


def test_type_may_be_a_list() -> None:
    assert check_output({"type": ["string", "null"]}, None) is None
    assert check_output({"type": ["string", "null"]}, 3) is not None
