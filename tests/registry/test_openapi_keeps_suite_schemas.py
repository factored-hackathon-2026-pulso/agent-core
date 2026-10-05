"""El OpenAPI del registry no pierde las propiedades de `Step` y `Expect` (un serializador de modelo las
volvía `additionalProperties: true`; `contracts --check` no lo ve: regenera con el mismo código)."""

import json
from pathlib import Path

import pytest

SCHEMAS = json.loads(Path("contracts/registry-openapi.json").read_text(encoding="utf-8"))["components"][
    "schemas"
]
BEFORE = {
    "Step": {"op", "text", "answer", "lang", "auth"},
    "Expect": {"outcome", "actions_verified", "escalated"},
}
NEW = {"Step": {"input"}, "Expect": {"suggestion_count", "suggestions"}}


@pytest.mark.parametrize("name", ["Step", "Expect"])
def test_the_committed_schema_keeps_its_typed_properties(name: str) -> None:
    schema = SCHEMAS[name]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == BEFORE[name] | NEW[name]
