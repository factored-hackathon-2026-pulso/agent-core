"""Comprobación 3 (`numbers`): toda cifra del texto es igual, exacta, a un valor de un hecho citado.

ADR 0011 y spec §3.1/§3.3: igualdad exacta en `Decimal` (sin tolerancia), sin contar los dígitos de tokens.
Decisiones vigentes (2026-09-29): el formato llega como dato opcional `ctx.number_format`; sin él solo se
aceptan lecturas inequívocas y una cifra ambigua (`1.234`) se rechaza aunque algún hecho la respalde; todo
número del texto cuenta igual (no hay lista de literales permitidos).

Las cifras del texto de una página citada (M12) respaldan como las de un hecho.

Un hecho `compute` respalda cifras como cualquier otro hecho citado; una cifra calculada cuyo hecho `compute`
no se cita no aparece en ningún hecho citado y por eso falla (T-M8-03). `ctx.fact_sources` no se usa para
filtrar: el criterio es el origen del número, no su semántica.
"""

import re
from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from agent_core.domain import JsonValue
from agent_core.response.numbers import scan_figures
from agent_core.response.types import Draft, Failure, ValidationContext

_CANONICAL_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_ISO_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")


def _walk(value: JsonValue) -> Iterable[Decimal | date]:
    """Valores numéricos y fechas ISO de un valor JSON de la vista `model` (los booleanos no son números)."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, Decimal):
        yield value
    elif isinstance(value, int):
        yield Decimal(value)
    elif isinstance(value, str):
        if _CANONICAL_NUMBER.fullmatch(value):
            yield Decimal(value)
        elif (iso := _ISO_DATE.fullmatch(value)) is not None:
            try:
                yield date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
            except ValueError:
                return
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)


def _page_figures(text: str, ctx: ValidationContext) -> Iterable[Decimal | date]:
    """Cifras de un texto de página. Una ambigua sin `number_format` no respalda nada (igual que en el
    borrador)."""
    return [f.value for f in scan_figures(text, ctx.number_format) if f.value is not None]


def check_numbers(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    figures = scan_figures(draft.text, ctx.number_format)
    if not figures:
        return []
    cited: set[Decimal | date] = set()
    for citation in draft.citations:
        if citation in ctx.facts_model_view:
            cited.update(_walk(ctx.facts_model_view[citation]))
        if citation in ctx.pages_model_view:
            page = ctx.pages_model_view[citation]
            cited.update(_walk(page))
            if isinstance(page, str):  # el texto de una página: sus cifras cuentan como las de un hecho (M12)
                cited.update(_page_figures(page, ctx))
    failures: list[Failure] = []
    # El detalle lleva la posición de la cifra, no su texto (lo escribe el modelo).
    for position, figure in enumerate(figures, start=1):
        if figure.ambiguous and ctx.number_format is None:
            failures.append(Failure(check="numbers", detail=f"cifra {position} ambigua"))
        elif figure.value is None:
            failures.append(Failure(check="numbers", detail=f"cifra {position} ilegible"))
        elif figure.value not in cited:
            failures.append(Failure(check="numbers", detail=f"cifra {position} sin fuente"))
    return failures
