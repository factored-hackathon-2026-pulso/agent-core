"""Comprobaciones del validador de M8: cada una es `check_x(draft, ctx) -> list[Failure]` (spec §9)."""

import re
from collections.abc import Callable, Iterable

from agent_core.domain import parse_page_ref
from agent_core.guards import detect_language
from agent_core.response.check_numbers import check_numbers
from agent_core.response.types import CheckId, Draft, Failure, ValidationContext
from agent_core.views import TOKEN_PATTERN

TOKEN_RE = re.compile(TOKEN_PATTERN)

Check = Callable[[Draft, ValidationContext], list[Failure]]


def check_format(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 1: invariantes del `Draft` (el parseo de la salida del gateway es `parse_draft`)."""
    if not draft.text.strip():
        return [Failure(check="format", detail="texto vacío")]
    return []


PII_DETAIL_PREFIX = "PII en claro"


def check_citations(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 2: cada cita a un hecho existe y está en `allowed`. Una falla por cita.

    Las citas con forma de página (`ruta@snapshot#ancla`) no se juzgan aquí sino en la 6 y la 7.

    El detalle lleva la posición de la cita (1-based), nunca su texto: lo escribe el modelo."""
    failures: list[Failure] = []
    for position, citation in enumerate(draft.citations, start=1):
        if parse_page_ref(citation) is not None:
            continue  # una cita a una página es de las comprobaciones 6 y 7 (M12)
        if citation not in ctx.facts_model_view and citation not in ctx.pages_model_view:
            failures.append(Failure(check="citations", detail=f"cita {position}: cita_inexistente"))
        elif citation not in ctx.allowed:
            failures.append(Failure(check="citations", detail=f"cita {position}: cita_no_permitida"))
    return failures


def check_tokens_pii(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 4: todo token existe en el vault y no hay PII en claro (solo rutas, nunca valores).

    El detalle de un token desconocido lleva su posición entre los tokens del texto, no el token."""
    failures = [
        Failure(check="tokens_pii", detail=f"token desconocido en la posición {position}")
        for position, token in enumerate(_unique(m.group(0) for m in TOKEN_RE.finditer(draft.text)), start=1)
        if not ctx.vault.exists(token)
    ]
    clear = ctx.find_clear_pii(draft.text)
    if clear:
        failures.append(Failure(check="tokens_pii", detail=f"{PII_DETAIL_PREFIX}: " + ", ".join(clear)))
    return failures


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def check_language(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 5: el idioma de mayor puntaje es el `locale` (`short`/`undetermined` no rechazan)."""
    decision = detect_language(draft.text, ctx.lang_cfg, ctx.lang_thresholds, list(ctx.supported_locales),
                               ctx.locale)
    if decision.decision in ("short", "undetermined") or not decision.top2:
        return []
    detected = decision.top2[0][0]
    if detected == ctx.locale:
        return []
    return [Failure(check="language", detail=f"idioma esperado {ctx.locale}, detectado {detected}")]


def check_page_citations(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 6 (M12): cada página citada está entre las páginas de un `save_as` de `knowledge_from`.

    Una falla por cita, con su posición (1-based) y nunca su texto: lo escribe el modelo."""
    return [
        Failure(check="page_citations", detail=f"cita {position}: página_no_listada")
        for position, citation in enumerate(draft.citations, start=1)
        if parse_page_ref(citation) is not None and citation not in ctx.page_refs
    ]


def check_page_audience(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 7 (M12): en una respuesta al cliente, cada página citada es `public` + `approved` y
    vigente
    al instante del `Clock`. Falla cerrado: una página sin metadatos o sin reloj no se acepta."""
    if not ctx.customer_facing:
        return []
    failures: list[Failure] = []
    for position, citation in enumerate(draft.citations, start=1):
        if parse_page_ref(citation) is None:
            continue
        reason = _page_problem(ctx, citation)
        if reason is not None:
            failures.append(Failure(check="page_audience", detail=f"cita {position}: {reason}"))
    return failures


def _page_problem(ctx: ValidationContext, ref: str) -> str | None:
    meta = ctx.pages_meta.get(ref)
    if meta is None:
        return "página_desconocida"
    if meta.status != "approved":
        return "página_no_aprobada"
    if meta.audience != "public":
        return "audiencia_no_pública"
    if ctx.now is None:
        return "sin_reloj"
    today = ctx.now.date()
    if (meta.valid_to is not None and today > meta.valid_to) or (
            meta.valid_from is not None and today < meta.valid_from):
        return "página_no_vigente"
    return None


CHECKS: tuple[tuple[CheckId, Check], ...] = (
    ("format", check_format),
    ("citations", check_citations),
    ("numbers", check_numbers),
    ("tokens_pii", check_tokens_pii),
    ("language", check_language),
    ("page_citations", check_page_citations),
    ("page_audience", check_page_audience),
)
