"""Validación de la salida del nodo `agent` contra su `output_schema` (m02 §3.7).

Subconjunto cerrado de JSON Schema, sin dependencias nuevas: `type`, `enum`, `properties`, `required`,
`additionalProperties` (booleano) e `items`. Las anotaciones (`description`, `title`, ...) se ignoran;
cualquier otra palabra clave falla cerrado (la salida se rechaza). El mensaje describe la ruta y la regla,
nunca el valor: puede volver al modelo como motivo de regeneración."""

from decimal import Decimal

from agent_core.domain import JsonValue

_ANNOTATIONS = frozenset({"description", "title", "default", "examples", "$schema", "$id"})
_SUPPORTED = frozenset({"type", "enum", "properties", "required", "additionalProperties", "items"})


def _is_type(value: JsonValue, name: str) -> bool:
    match name:
        case "string":
            return isinstance(value, str)
        case "boolean":
            return isinstance(value, bool)
        case "null":
            return value is None
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "number":
            return isinstance(value, int | Decimal) and not isinstance(value, bool)
        case "object":
            return isinstance(value, dict)
        case "array":
            return isinstance(value, list)
    return False


def check_output(schema: dict[str, JsonValue], value: JsonValue, path: str = "") -> str | None:
    """`None` si `value` cumple `schema`; si no, el primer motivo (ruta y regla, sin el valor)."""
    where = path or "/"
    unknown = sorted(k for k in schema if k not in _SUPPORTED and k not in _ANNOTATIONS)
    if unknown:
        return f"{where}: palabra clave no soportada {unknown[0]!r}"
    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        if not any(isinstance(n, str) and _is_type(value, n) for n in names):
            return f"{where}: tipo distinto de {expected}"
    options = schema.get("enum")
    if isinstance(options, list) and value not in options:
        return f"{where}: fuera de los valores permitidos"
    if isinstance(value, dict):
        return _check_object(schema, value, path)
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            error = check_output(schema["items"], item, f"{path}/{index}")  # type: ignore[arg-type]
            if error is not None:
                return error
    return None


def _check_object(schema: dict[str, JsonValue], value: dict[str, JsonValue], path: str) -> str | None:
    properties = schema.get("properties")
    props = properties if isinstance(properties, dict) else {}
    required = schema.get("required")
    for name in required if isinstance(required, list) else []:
        if isinstance(name, str) and name not in value:
            return f"{path or '/'}: falta la propiedad {name!r}"
    for name in sorted(value):
        child = props.get(name)
        if isinstance(child, dict):
            error = check_output(child, value[name], f"{path}/{name}")
            if error is not None:
                return error
        elif schema.get("additionalProperties") is False:
            return f"{path or '/'}: propiedad no permitida {name!r}"
    return None
