"""`KnowledgeService.read` (m12 §3.1): recupera las páginas fijas de un nodo `knowledge`, las filtra dos veces
según el `purpose`, las proyecta a la vista `model` y las deja en `RunState.pages`. Único escritor de
`pages`."""

from dataclasses import dataclass, field
from typing import Literal

from agent_core.domain import (
    EngineEvent,
    FilteredPage,
    KnowledgeNode,
    KnowledgeRead,
    KnowledgeReadPayload,
    PageMeta,
    PageRecord,
    PageSpec,
    PageView,
    RunState,
    page_ref,
    parse_page_spec,
)
from agent_core.knowledge.context import KnowledgeContext
from agent_core.knowledge.filters import FilterReason, FilterRequest, filter_reason
from agent_core.knowledge.sections import extract_section
from agent_core.ports import AuthzPort, IdKind, KnowledgeSource

_UNTRUSTED_FIELD = "content"
_PAGE_SOURCE = "knowledge.page"
_EXTERNAL_MARK = "://"

Reason = Literal["source_unavailable", "navigate_unavailable", "no_snapshot"]


@dataclass(slots=True)
class _Outcome:
    delivered: list[PageView] = field(default_factory=list)
    filtered: list[FilteredPage] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


class KnowledgeService:
    """Lee páginas de un `KnowledgeSource` con la vista que calcula el `AuthzPort`."""

    def __init__(self, source: KnowledgeSource, authz: AuthzPort) -> None:
        self._source = source
        self._authz = authz

    def read(self, node: KnowledgeNode, state: RunState, ctx: KnowledgeContext,
             ) -> tuple[RunState, str, list[EngineEvent]]:
        """`(estado, resultado, eventos)`; resultado: `ok`, `not_found` o `denied`. Nunca lanza por la
        fuente."""
        cfg = node.config
        if cfg.mode == "navigate":  # todavía no se ejecuta (ADR 0015, corte MVP): falla cerrado
            return self._done(node, state, ctx, _Outcome(), reason="navigate_unavailable")
        if ctx.release.knowledge_snapshot is None:
            return self._done(node, state, ctx, _Outcome(), reason="no_snapshot")
        snapshot = str(ctx.release.knowledge_snapshot)
        view = self._authz.knowledge_view(state.principal, cfg.purpose)
        specs = [parse_page_spec(text) for text in cfg.pages]
        outcome = _Outcome()
        if not view.audiences:  # el PEP no le da ninguna página a este principal para este propósito
            outcome.filtered = [FilteredPage(ref=page_ref(s.path, snapshot, s.anchor), reason="view")
                                for s in specs]
            return self._done(node, state, ctx, outcome)
        request = FilterRequest(cfg.purpose, view, snapshot, state.locale, ctx.clock.now().date())
        try:
            for spec in specs:
                self._fetch(spec, request, ctx, outcome)
        except Exception:  # la fuente cayó: sale por not_found y la auditoría lo distingue
            return self._done(node, state, ctx, _Outcome(), reason="source_unavailable")
        return self._done(node, state, ctx, outcome)

    # -- una página -----------------------------------------------------------------------------------------

    def _fetch(self, spec: PageSpec, req: FilterRequest, ctx: KnowledgeContext, out: _Outcome) -> None:
        ref = page_ref(spec.path, req.snapshot, spec.anchor)
        record = self._source.read(spec.path, req.snapshot, req.view)
        if record is None:  # ausente o fuera de la vista del servicio: no se distinguen
            out.missing.append(ref)
            return
        reason = filter_reason(record.meta, req)
        if reason == "lang":
            record, reason = self._translation(record, req)
        if record is None or reason is not None:
            out.filtered.append(FilteredPage(ref=ref, reason=reason or "lang"))
            return
        text = record.content
        if spec.anchor is not None:
            section = extract_section(text, spec.anchor)
            if section is None:
                out.missing.append(ref)
                return
            text = section
        meta = record.meta.model_copy(update={"anchor": spec.anchor})
        out.delivered.append(PageView(ref=page_ref(meta.path, req.snapshot, spec.anchor), meta=meta,
                                      content_model=self._project(text, meta, ctx)))

    def _translation(self, record: PageRecord, req: FilterRequest,
                     ) -> tuple[PageRecord | None, FilterReason | None]:
        """La versión de la página en el idioma del turno (`translation_of` en cualquiera de los dos
        sentidos)."""
        for meta in self._source.index(req.snapshot, req.view):
            if meta.lang != req.locale or not _linked(record.meta, meta):
                continue
            found = self._source.read(meta.path, req.snapshot, req.view)
            if found is not None and filter_reason(found.meta, req) is None:
                return found, None
        return None, "lang"

    def _project(self, text: str, meta: PageMeta, ctx: KnowledgeContext) -> str:
        """Vista `model` (M7): la PII pasa a tokens; una fuente externa además se envuelve como
        `untrusted_text`."""
        if any(_EXTERNAL_MARK in ref for ref in meta.source_refs):
            projected = ctx.views.project(
                {_UNTRUSTED_FIELD: text}, _PAGE_SOURCE, [_UNTRUSTED_FIELD], ctx.vault)
            model = projected.model
            assert isinstance(model, dict)
            wrapped = model[_UNTRUSTED_FIELD]
            assert isinstance(wrapped, str)
            return wrapped
        return ctx.views.tokenize_text(text, ctx.vault)

    # -- resultado y evento ---------------------------------------------------------------------------------

    def _done(self, node: KnowledgeNode, state: RunState, ctx: KnowledgeContext, out: _Outcome,
              reason: Reason | None = None) -> tuple[RunState, str, list[EngineEvent]]:
        """`denied` si M12 retuvo alguna página; `not_found` si falta alguna o el nodo no pudo leer; si no,
        `ok`.

        Un resultado que no es `ok` no deja nada bajo `save_as` (nada viejo queda citable tras un fallo)."""
        save_as = node.config.save_as
        if out.filtered:
            result = "denied"
        elif out.missing or reason is not None:
            result = "not_found"
        else:
            result = "ok"
        pages = {name: views for name, views in state.pages.items() if name != save_as}
        if result == "ok":
            pages[save_as] = out.delivered
        payload = KnowledgeReadPayload.model_validate({
            "node_id": node.id, "purpose": node.config.purpose, "result": result,
            "refs": [page.ref for page in out.delivered] if result == "ok" else [],
            "filtered_out": out.filtered, "missing": out.missing, "reason": reason})
        event = KnowledgeRead.model_validate({
            "event_id": ctx.ids.new_id(IdKind.event), "run_id": state.run_id, "turn_id": ctx.turn_id,
            "session_id": state.session_id, "release": state.release, "ts": ctx.clock.now(),
            "payload": payload})
        return state.model_copy(update={"pages": pages}), result, [event]


def _linked(original: PageMeta, other: PageMeta) -> bool:
    return other.translation_of == original.path or original.translation_of == other.path
