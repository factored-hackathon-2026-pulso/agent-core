"""Renderizador mínimo de plantillas de M8 (spec §3.2): solo `{{ facts.<nombre>.value(.<campo>)* }}`.

M8 no puede importar `flows`/`interpreter`; la plantilla de respaldo solo lee hechos (M1 §3.3).
"""

import re
from collections.abc import Mapping
from decimal import Decimal

from agent_core.domain import JsonValue

_EXPR = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_PATH = re.compile(r"facts\.([a-z0-9_]+)\.value((?:\.[A-Za-z0-9_]+)*)")


class TemplateUnavailable(Exception):
    """La plantilla no puede renderizarse (locale o ruta ausente, expresión no admitida o mal formada)."""


def _scalar(value: JsonValue) -> str:
    if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
        raise TemplateUnavailable("la ruta no apunta a un valor escalar")
    return str(value) if isinstance(value, (Decimal, int)) else value


def _lookup(path: str, facts_by_name: Mapping[str, JsonValue]) -> str:
    match = _PATH.fullmatch(path)
    if match is None:
        raise TemplateUnavailable("expresión de plantilla no admitida en M8")
    name, rest = match.group(1), match.group(2)
    current: JsonValue = facts_by_name.get(name)
    if not isinstance(current, dict) or "value" not in current:
        raise TemplateUnavailable(f"hecho ausente: {name}")
    current = current["value"]
    for key in [part for part in rest.split(".") if part]:
        if not isinstance(current, dict) or key not in current:
            raise TemplateUnavailable(f"ruta ausente: facts.{name}.value{rest}")
        current = current[key]
    return _scalar(current)


def render_template_text(text: str, facts_model_view_by_name: Mapping[str, JsonValue]) -> str:
    """Sustituye cada `{{ ruta }}`; `facts_model_view_by_name[nombre]` es `{"value": ...}` (vista `model`)."""
    leftover = _EXPR.sub("", text)
    if "{{" in leftover or "}}" in leftover:
        raise TemplateUnavailable("expresión de plantilla mal formada")
    return _EXPR.sub(lambda m: _lookup(m.group(1).strip(), facts_model_view_by_name), text)
