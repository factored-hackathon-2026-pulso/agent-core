"""Interpretación de la salida estructurada del modelo (spec §3.1 paso 7)."""

import re

from agent_core.domain import JsonValue, check_output, loads

# Un único bloque de código que es todo el contenido: los modelos en modo `prompted` lo usan a menudo.
_FENCE = re.compile(r"\A```[A-Za-z]*[ \t]*\n(?P<body>.*?)\n?```\Z", re.DOTALL)


class OutputError(Exception):
    """La salida no es JSON o no cumple el esquema. `reason` describe la ruta y la regla, nunca el valor."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_output(content: str, schema: dict[str, JsonValue]) -> JsonValue:
    """Interpreta `content` como JSON (aceptando un único bloque de código) y lo valida contra `schema`."""
    text = content.strip()
    fenced = _FENCE.match(text)
    if fenced is not None:
        text = fenced["body"]
    try:
        value = loads(text)
    except ValueError:
        raise OutputError("el contenido no es JSON") from None
    error = check_output(schema, value)
    if error is not None:
        raise OutputError(error)
    return value
