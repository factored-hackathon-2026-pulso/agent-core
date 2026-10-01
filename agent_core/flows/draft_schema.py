"""Esquema de la salida de un nodo `agent` cuando alimenta una escritura `draft` (ADR 0019 §1, enmienda D9).

Es la única salida de un `agent` que puede leerse en `tool.args`: el registry la valida otra vez con su
esquema estricto y sus límites, y la aprobación humana de la propuesta es el gate. Dentro del subconjunto
cerrado de `agent_core.domain.schema`."""

from agent_core.domain import JsonValue

_TEXT: dict[str, JsonValue] = {"type": "string"}

DRAFT_OUTPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["changes"],
    "properties": {
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "content", "docs"],
                "properties": {
                    "kind": {"type": "string"},
                    "content": {"type": "object"},
                    "docs": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["description", "rationale", "changelog"],
                        "properties": {"description": _TEXT, "rationale": _TEXT, "changelog": _TEXT},
                    },
                },
            },
        },
    },
}
