from decimal import Decimal

import pytest

from agent_core.decision.schema import check_schema_supported, validate_output
from agent_core.decision.types import DecisionConfigError
from agent_core.domain import JsonValue

SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["command"],
    "properties": {
        "command": {"type": "string", "enum": ["affirm", "deny"]},
        "flow": {"type": "string"},
        "amount": {"type": "number"},
        "flag": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "null"},
        "slots": {"type": "object", "additionalProperties": True},
        "nested": {
            "type": "object", "additionalProperties": False, "required": ["a"],
            "properties": {"a": {"type": "string"}},
        },
    },
}


def test_valid_object_has_no_errors() -> None:
    value: JsonValue = {"command": "affirm", "amount": Decimal("1.5"), "tags": ["x"], "slots": {"k": 1}}
    assert validate_output(value, SCHEMA) == []


def test_missing_required_field() -> None:
    assert validate_output({"flow": "f"}, SCHEMA) == ["$.command"]


def test_extra_field_is_rejected_because_struct_is_closed() -> None:
    errors = validate_output({"command": "affirm", "razonamiento": "SECRETO-XYZ"}, SCHEMA)
    assert errors and "SECRETO-XYZ" not in " ".join(errors)


def test_value_outside_enum() -> None:
    assert validate_output({"command": "maybe"}, SCHEMA) == ["$.command"]


def test_wrong_type() -> None:
    assert validate_output({"command": "affirm", "flow": 3}, SCHEMA) == ["$.flow"]


def test_bool_is_not_a_number_and_number_accepts_int_and_decimal() -> None:
    assert validate_output({"command": "affirm", "amount": True}, SCHEMA) == ["$.amount"]
    assert validate_output({"command": "affirm", "amount": 3}, SCHEMA) == []
    assert validate_output({"command": "affirm", "amount": Decimal("3.0")}, SCHEMA) == []


def test_bool_and_null_types() -> None:
    assert validate_output({"command": "affirm", "flag": 1}, SCHEMA) == ["$.flag"]
    assert validate_output({"command": "affirm", "note": None, "flag": False}, SCHEMA) == []


def test_enum_does_not_confuse_bool_with_int() -> None:
    schema: JsonValue = {"type": "object", "additionalProperties": False, "required": ["n"],
                         "properties": {"n": {"enum": [1, 2]}}}
    assert validate_output({"n": True}, schema) == ["$.n"]  # type: ignore[arg-type]


def test_nested_object_and_array_items_paths() -> None:
    assert validate_output({"command": "affirm", "nested": {}}, SCHEMA) == ["$.nested.a"]
    assert validate_output({"command": "affirm", "tags": ["a", 2]}, SCHEMA) == ["$.tags[1]"]


def test_free_object_only_when_explicit() -> None:
    assert validate_output({"command": "affirm", "slots": {"cualquier": {"cosa": [1]}}}, SCHEMA) == []


def test_non_object_root() -> None:
    assert validate_output("x", SCHEMA) == ["$"]


@pytest.mark.parametrize("bad", [
    {"$ref": "#/defs/x"},
    {"oneOf": [{"type": "string"}]},
    {"type": "string", "pattern": "^a"},
    {"type": "object", "properties": {}},  # objeto sin additionalProperties: no es struct cerrado
])
def test_unsupported_schema_is_a_config_error(bad: dict[str, JsonValue]) -> None:
    with pytest.raises(DecisionConfigError):
        check_schema_supported(bad)
    with pytest.raises(DecisionConfigError):
        validate_output({}, bad)


def test_supported_schema_passes_check() -> None:
    check_schema_supported(SCHEMA)


def test_error_messages_never_contain_values() -> None:
    errors = validate_output({"command": "SECRETO-XYZ", "flow": ["SECRETO-XYZ"]}, SCHEMA)
    assert errors and "SECRETO-XYZ" not in " ".join(errors)
