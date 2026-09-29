"""Validador de un subconjunto de JSON Schema para `output_schema` (struct cerrado).

Subconjunto: `type` (object, string, array, boolean, number, null), `enum`, `properties`, `required`,
`additionalProperties` (obligatorio en cada objeto), `items` y las anotaciones `title`/`description`.
Los errores son rutas (`$.slots.amount`): nunca incluyen valores ni nombres de claves inesperadas."""

from decimal import Decimal

from agent_core.decision.types import DecisionConfigError
from agent_core.domain import JsonValue

_TYPES = frozenset({"object", "string", "array", "boolean", "number", "null"})
_KEYWORDS = frozenset({"type", "enum", "properties", "required", "additionalProperties", "items", "title",
                       "description"})


def check_schema_supported(schema: dict[str, JsonValue]) -> None:
    """Lanza `DecisionConfigError` si el esquema usa algo fuera del subconjunto."""
    _check(schema, "$")


def validate_output(value: JsonValue, schema: dict[str, JsonValue]) -> list[str]:
    """Rutas con error; vacía = válido. Lanza `DecisionConfigError` si el esquema no está soportado."""
    check_schema_supported(schema)
    errors: list[str] = []
    _validate(value, schema, "$", errors)
    return errors


def _check(schema: JsonValue, path: str) -> None:
    if not isinstance(schema, dict):
        raise DecisionConfigError(f"esquema no soportado en {path}: debe ser un objeto")
    unknown = sorted(set(schema) - _KEYWORDS)
    if unknown:
        raise DecisionConfigError(f"esquema no soportado en {path}: palabra clave {unknown[0]!r}")
    type_ = schema.get("type")
    if type_ is not None and type_ not in _TYPES:
        raise DecisionConfigError(f"esquema no soportado en {path}: type inválido")
    if type_ == "object":
        additional = schema.get("additionalProperties")
        if not isinstance(additional, bool):
            raise DecisionConfigError(
                f"esquema no soportado en {path}: los objetos declaran additionalProperties (true o false)")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise DecisionConfigError(f"esquema no soportado en {path}: properties debe ser un objeto")
        for name, sub in properties.items():
            _check(sub, f"{path}.{name}")
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
            raise DecisionConfigError(f"esquema no soportado en {path}: required debe ser lista de strings")
    elif "properties" in schema or "required" in schema or "additionalProperties" in schema:
        raise DecisionConfigError(f"esquema no soportado en {path}: propiedades de objeto en otro type")
    if type_ == "array" and "items" in schema:
        _check(schema["items"], f"{path}[]")
    elif "items" in schema:
        raise DecisionConfigError(f"esquema no soportado en {path}: items fuera de un array")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise DecisionConfigError(f"esquema no soportado en {path}: enum debe ser una lista")


def _same(a: JsonValue, b: JsonValue) -> bool:
    """Igualdad estricta: `True` no es `1`."""
    return isinstance(a, bool) == isinstance(b, bool) and a == b


def _type_ok(value: JsonValue, type_: str) -> bool:
    match type_:
        case "object":
            return isinstance(value, dict)
        case "string":
            return isinstance(value, str)
        case "array":
            return isinstance(value, list)
        case "boolean":
            return isinstance(value, bool)
        case "number":
            return isinstance(value, int | Decimal) and not isinstance(value, bool)
        case _:
            return value is None


def _validate(value: JsonValue, schema: dict[str, JsonValue], path: str, errors: list[str]) -> None:
    type_ = schema.get("type")
    if isinstance(type_, str) and not _type_ok(value, type_):
        errors.append(path)
        return
    enum = schema.get("enum")
    if isinstance(enum, list) and not any(_same(value, option) for option in enum):
        errors.append(path)
        return
    if isinstance(value, dict) and type_ == "object":
        properties = schema.get("properties", {})
        assert isinstance(properties, dict)
        required = schema.get("required", [])
        assert isinstance(required, list)
        for name in required:
            if name not in value:
                errors.append(f"{path}.{name}")
        for name, item in value.items():
            sub = properties.get(name)
            if isinstance(sub, dict):
                _validate(item, sub, f"{path}.{name}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.<additional>")
    elif isinstance(value, list) and type_ == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate(item, items, f"{path}[{index}]", errors)
