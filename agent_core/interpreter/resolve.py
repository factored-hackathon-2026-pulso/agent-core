"""Resolución de rutas, argumentos y plantillas (M2 §3.2). La gramática es la de M1 (`parse_path`)."""

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal

from agent_core.domain import JsonValue, RunState, dumps
from agent_core.flows import Path, parse_path, value_paths


class MissingPath(LookupError):
    """La ruta no existe en el estado (o es un slot `claimed`). Es una rama del flow, no un bug (D7)."""


def walk(value: JsonValue, keys: Sequence[str], raw: str) -> JsonValue:
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise MissingPath(raw)
    return value


def resolve_path(state: RunState, path: Path) -> JsonValue:
    """Valor en vista `full`. Solo para tools, `rule` y `end`; a modelos y mensajes llega vía `Projector`."""
    name = path.name or ""
    if path.ns == "slots":
        found = state.slots.get(name)
        if found is None or found.status != "validated":
            raise MissingPath(path.raw)
        return found.value
    if path.ns == "facts":
        fact = state.facts.get(name)
        if fact is None or not path.rest or path.rest[0] != "value":
            raise MissingPath(path.raw)
        return walk(fact.value, path.rest[1:], path.raw)
    if path.ns == "decisions":
        decision = state.decisions.get(name)
        if decision is None:
            raise MissingPath(path.raw)
        return walk(decision.value, path.rest, path.raw)
    raise MissingPath(path.raw)


def parse_runtime_path(text: str) -> Path | None:
    """`None` si es un literal; una ruta mal formada es `MissingPath` (nunca `ValueError`)."""
    try:
        return parse_path(text)
    except ValueError:
        raise MissingPath(text) from None


def resolve_value(state: RunState, value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        path = parse_runtime_path(value)
        return value if path is None else resolve_path(state, path)
    if isinstance(value, list):
        return [resolve_value(state, item) for item in value]
    if isinstance(value, dict):
        return {key: resolve_value(state, item) for key, item in value.items()}
    return value


def resolve_args(state: RunState, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {key: resolve_value(state, item) for key, item in args.items()}


def input_ids(state: RunState, args: dict[str, JsonValue]) -> list[str]:
    """`fact_id` y `decision_id` que leen los `args` (procedencia de `compute`, M2 §3.3)."""
    ids: list[str] = []
    for path in value_paths(args, strict=False):
        name = path.name or ""
        if path.ns == "facts" and name in state.facts:
            ident = state.facts[name].fact_id
        elif path.ns == "decisions" and name in state.decisions:
            ident = state.decisions[name].decision_id
        else:
            continue
        if ident not in ids:
            ids.append(ident)
    return ids


_VAR = re.compile(r"\{\{(?P<body>[^{}]*)\}\}")


def _text(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    return dumps(value)


def render_template(text: str, values: Mapping[str, JsonValue]) -> str:
    """Sustituye cada `{{ ruta }}` por `values[ruta]`. Las claves son las rutas ya recortadas."""
    return _VAR.sub(lambda match: _text(values[match["body"].strip()]), text)
