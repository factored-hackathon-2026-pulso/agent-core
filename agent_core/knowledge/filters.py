"""Segundo filtro de M12 (m12 §3.1 paso 3): lo que se le puede mostrar a cada `purpose`.

El primero lo hace el servicio con la `KnowledgeView`; este se repite sin confiar en él (ADR 0015). Es puro:
la fecha de vigencia la trae quien llama, salida del `Clock`."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from agent_core.domain import KnowledgeView, PageMeta, Purpose

FilterReason = Literal["audience", "not_approved", "expired", "not_yet_valid", "lang", "snapshot"]


@dataclass(frozen=True, slots=True)
class FilterRequest:
    purpose: Purpose
    view: KnowledgeView
    snapshot: str
    locale: str
    today: date


def filter_reason(meta: PageMeta, req: FilterRequest) -> FilterReason | None:
    """Por qué `meta` no se entrega, o `None` si pasa. Es la intersección de la vista y del `purpose`.

    - `customer_answer`: `public` + `approved` + vigente + `lang` del turno (la traducción se busca fuera);
    - `advisor_view`: `public` o `internal`;
    - `agent_guidance`: cualquiera, pero nunca citable al cliente (lo comprueba M8).
    """
    if meta.snapshot != req.snapshot:
        return "snapshot"
    if meta.audience not in req.view.audiences:
        return "audience"
    if req.view.approved_only and meta.status != "approved":
        return "not_approved"
    match req.purpose:
        case "customer_answer":
            if meta.audience != "public":
                return "audience"
            if meta.status != "approved":
                return "not_approved"
            if meta.valid_to is not None and req.today > meta.valid_to:
                return "expired"
            if meta.valid_from is not None and req.today < meta.valid_from:
                return "not_yet_valid"
            if meta.lang != req.locale:
                return "lang"
        case "advisor_view":
            if meta.audience not in ("public", "internal"):
                return "audience"
        case "agent_guidance":
            pass
    return None
