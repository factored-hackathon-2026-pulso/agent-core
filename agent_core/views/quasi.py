"""Operaciones genéricas sobre `pii_quasi` (M7 §3.2). Qué operación aplica a cada campo es dato (`QuasiRule`).

Falla cerrado: un valor que la operación no entiende se elimina de la vista."""

import re
from datetime import date
from typing import Final

from agent_core.domain import JsonValue
from agent_core.views.classification import QuasiRule

_DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")


class Dropped:
    """Marca de un campo eliminado de la vista (no es un valor JSON)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "DROPPED"


DROPPED: Final = Dropped()


def _birth_date(value: JsonValue) -> date | None:
    if not isinstance(value, str):
        return None
    match = _DATE_RE.match(value)
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def apply_quasi(value: JsonValue, rule: QuasiRule, today: date) -> JsonValue | Dropped:
    if rule.op == "age_bucket":
        born = _birth_date(value)
        if born is not None and born <= today:
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
            low = age // rule.width * rule.width
            return f"{low}-{low + rule.width - 1}"
    return DROPPED
