"""Gramática única de rutas y de variables de plantilla (M1 §3.2, §3.3). M2 resuelve con las mismas funciones.

Todas las expresiones regulares son lineales (sin cuantificadores anidados ni alternancias ambiguas) y se
aplican con `fullmatch`, de modo que una entrada hostil no puede causar retroceso catastrófico.
"""

import re
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

from agent_core.domain import JsonValue

Namespace = Literal["slots", "facts", "decisions", "readback"]

_NAME = r"[a-z][a-z0-9_]*"
_FIELD = r"[A-Za-z0-9_]+"
_PATTERNS: dict[str, re.Pattern[str]] = {
    "slots": re.compile(rf"slots\.(?P<name>{_NAME})"),
    "facts": re.compile(rf"facts\.(?P<name>{_NAME})(?P<rest>\.value(?:\.{_FIELD})*)?"),
    "decisions": re.compile(rf"decisions\.(?P<name>{_NAME})(?P<rest>(?:\.{_FIELD})+)"),
    "readback": re.compile(rf"readback(?P<rest>(?:\.{_FIELD})*)"),
}
_PREFIXES = ("slots.", "facts.", "decisions.", "readback.")
_VAR = re.compile(r"\{\{(?P<body>[^{}]*)\}\}")


class Path(BaseModel):
    """Ruta parseada. `name` es el hecho, slot o decisión que lee (None en `readback`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ns: Namespace
    name: str | None
    rest: tuple[str, ...] = ()
    raw: str

    @property
    def whole_fact(self) -> bool:
        """`facts.<n>` sin `.value`: solo válido en `allowed_facts`."""
        return self.ns == "facts" and not self.rest


def looks_like_path(text: str) -> bool:
    """True si `text` empieza como una ruta (es decir, no es un literal)."""
    return text == "readback" or text.startswith(_PREFIXES)


def parse_path(text: str) -> Path | None:
    """None si `text` es un literal. `ValueError` si parece ruta y no cumple la gramática."""
    if not looks_like_path(text):
        return None
    ns = text.split(".", 1)[0]
    match = _PATTERNS[ns].fullmatch(text)
    if match is None:
        raise ValueError(f"ruta mal formada: {text!r}")
    groups = match.groupdict()
    rest = tuple(part for part in (groups.get("rest") or "").split(".") if part)
    return Path(ns=cast(Namespace, ns), name=groups.get("name"), rest=rest, raw=text)


def _strings(value: JsonValue) -> list[str]:
    """Strings del valor en orden de aparición; iterativo, sin límite de profundidad."""
    found: list[str] = []
    stack: list[JsonValue] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, list):
            stack.extend(reversed(item))
        elif isinstance(item, dict):
            stack.extend(reversed(list(item.values())))
    return found


def value_paths(value: JsonValue, *, strict: bool = True) -> list[Path]:
    """Rutas en un valor de `args` (recursivo). Con `strict=False` omite las mal formadas."""
    paths: list[Path] = []
    for text in _strings(value):
        try:
            path = parse_path(text)
        except ValueError:
            if strict:
                raise
            continue
        if path is not None:
            paths.append(path)
    return paths


def bad_paths(value: JsonValue) -> list[str]:
    """Strings que parecen rutas y no parsean."""
    bad: list[str] = []
    for text in _strings(value):
        try:
            parse_path(text)
        except ValueError:
            bad.append(text)
    return bad


def _no_braces(chunk: str) -> None:
    if "{{" in chunk or "}}" in chunk:
        raise ValueError("'{{' o '}}' sin una variable válida")


def template_vars(text: str) -> frozenset[str]:
    """Rutas de las variables `{{ ruta }}` de una plantilla (M1 §3.3)."""
    found: set[str] = set()
    position = 0
    for match in _VAR.finditer(text):
        _no_braces(text[position : match.start()])
        body = match["body"].strip()
        path = parse_path(body) if body else None
        if path is None:
            raise ValueError(f"variable de plantilla inválida: {{{{ {body} }}}}")
        found.add(path.raw)
        position = match.end()
    _no_braces(text[position:])
    return frozenset(found)
