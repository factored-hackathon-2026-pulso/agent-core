"""Mecánica del parser numérico de M8 (spec §3.3): cifras, porcentajes y fechas del texto, en `Decimal`.

No decide qué formato usa nadie ni qué cifras cuentan: el formato es un dato (`NumberFormat`) que llega en el
contexto de validación; sin él solo se aceptan lecturas inequívocas. Nunca `float`.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from agent_core.views import TOKEN_PATTERN

TOKEN_RE = re.compile(TOKEN_PATTERN)

_SEPARATORS = frozenset({".", ","})

# Meses en ES y PT (abril y agosto se escriben igual en ambos idiomas).
MESES: dict[str, int] = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "janeiro": 1, "fevereiro": 2, "março": 3, "maio": 5, "junho": 6, "julho": 7, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}
_MONTH_NAMES = sorted(MESES, key=len, reverse=True)


@dataclass(frozen=True, slots=True)
class NumberFormat:
    """Separadores de un formato numérico: `(".", ",")` es `1.234,56`; `(",", ".")` es `1,234.56`."""

    thousands: str
    decimal: str

    def __post_init__(self) -> None:
        valid = self.thousands in _SEPARATORS and self.decimal in _SEPARATORS
        if not valid or self.thousands == self.decimal:
            raise ValueError("NumberFormat necesita '.' y ',' distintos como miles y decimal")


@dataclass(frozen=True, slots=True)
class Figure:
    raw: str
    kind: Literal["amount", "percent", "date"]
    value: Decimal | date | None  # None = ilegible con este formato (o ambigua sin formato)
    ambiguous: bool  # un solo separador seguido de exactamente 3 dígitos (`1.234`): dos lecturas posibles


_CURRENCY = r"(?:USD|COP|MXN|ARS)"
_MONTHS_ALT = "|".join(_MONTH_NAMES)
_FIGURE_RE = re.compile(
    rf"""
    (?P<d_slash>(?<!\d)(?P<ds_d>\d{{1,2}})/(?P<ds_m>\d{{1,2}})/(?P<ds_y>\d{{4}})(?!\d))
  | (?P<d_iso>(?<!\d)(?P<di_y>\d{{4}})-(?P<di_m>\d{{2}})-(?P<di_d>\d{{2}})(?!\d))
  | (?P<d_text>(?<!\d)(?P<dt_d>\d{{1,2}})\s+de\s+(?P<dt_m>{_MONTHS_ALT})\s+de\s+(?P<dt_y>\d{{4}})(?!\d))
  | (?P<num>(?P<pre>(?:\$|(?<![A-Za-z]){_CURRENCY}(?![A-Za-z]))\s?)?
      (?P<n>\d[\d.,]*\d|\d)
      (?P<post>\s?(?:%|(?<![A-Za-z]){_CURRENCY}(?![A-Za-z])))?)
    """,
    re.VERBOSE | re.IGNORECASE,
)

_INT_LEADING = re.compile(r"[1-9]\d{0,2}")
_GROUP = re.compile(r"\d{3}")


def strip_tokens(text: str) -> str:
    """Sustituye cada token de M7 por un espacio: sus dígitos no son cifras (T-M8-10)."""
    return TOKEN_RE.sub(" ", text)


def _grouped(integer: str, thousands: str) -> str | None:
    """Parte entera sin separadores de miles, o `None` si la agrupación no es válida."""
    groups = integer.split(thousands)
    if len(groups) == 1:
        return groups[0]
    first, rest = groups[0], groups[1:]
    if not (1 <= len(first) <= 3) or not all(_GROUP.fullmatch(g) for g in rest):
        return None
    return "".join(groups)


def _single_separator_is_ambiguous(digits: str, sep: str) -> bool:
    integer, _, fraction = digits.partition(sep)
    return len(fraction) == 3 and _INT_LEADING.fullmatch(integer) is not None


def _parse_with_format(digits: str, fmt: NumberFormat) -> Decimal | None:
    if digits.count(fmt.decimal) > 1:
        return None
    integer, dec_sep, fraction = digits.partition(fmt.decimal)
    if fmt.thousands in fraction:
        return None
    whole = _grouped(integer, fmt.thousands)
    if whole is None:
        return None
    return Decimal(f"{whole}.{fraction}") if dec_sep else Decimal(whole)


def _parse_unambiguous(digits: str) -> Decimal | None:
    """Sin formato: solo lecturas con una única interpretación; `None` si es ambigua o está mal formada."""
    kinds = {c for c in digits if c in _SEPARATORS}
    if not kinds:
        return Decimal(digits)
    if len(kinds) == 2:
        decimal = digits[max(digits.rfind("."), digits.rfind(","))]
        thousands = "," if decimal == "." else "."
        return _parse_with_format(digits, NumberFormat(thousands=thousands, decimal=decimal))
    (sep,) = kinds
    if digits.count(sep) > 1:
        whole = _grouped(digits, sep)
        return None if whole is None else Decimal(whole)
    if _single_separator_is_ambiguous(digits, sep):
        return None
    integer, _, fraction = digits.partition(sep)
    return Decimal(f"{integer}.{fraction}")


def _is_ambiguous(digits: str) -> bool:
    kinds = {c for c in digits if c in _SEPARATORS}
    if len(kinds) != 1:
        return False
    (sep,) = kinds
    return digits.count(sep) == 1 and _single_separator_is_ambiguous(digits, sep)


def _make_date(year: str, month: int, day: str) -> date | None:
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def scan_figures(text: str, fmt: NumberFormat | None) -> list[Figure]:
    """Cifras del texto en orden de aparición, sin contar los dígitos de tokens.

    Con `fmt` se leen según ese formato; con `fmt=None` solo las lecturas inequívocas (una cifra ambigua
    trae `ambiguous=True` y `value=None`). Determinista y sin efectos."""
    figures: list[Figure] = []
    for match in _FIGURE_RE.finditer(strip_tokens(text)):
        raw = match.group(0).strip()
        if match.group("d_slash"):
            month = int(match.group("ds_m"))
            value = _make_date(match.group("ds_y"), month, match.group("ds_d")) if 1 <= month <= 12 else None
            figures.append(Figure(raw, "date", value, False))
        elif match.group("d_iso"):
            value = _make_date(match.group("di_y"), int(match.group("di_m")), match.group("di_d"))
            figures.append(Figure(raw, "date", value, False))
        elif match.group("d_text"):
            value = _make_date(match.group("dt_y"), MESES[match.group("dt_m").lower()], match.group("dt_d"))
            figures.append(Figure(raw, "date", value, False))
        else:
            digits = match.group("n")
            post = match.group("post") or ""
            kind: Literal["amount", "percent"] = "percent" if post.strip() == "%" else "amount"
            parsed = _parse_with_format(digits, fmt) if fmt is not None else _parse_unambiguous(digits)
            figures.append(Figure(raw, kind, parsed, _is_ambiguous(digits)))
    return figures
