"""G0-17 a G0-21: reglas del nodo `knowledge` y de las respuestas que citan sus páginas (M1 §3.4, M12 §3.3).

G0-18 y G0-21 miran solo el flow (`FLOW_RULES`). G0-17, G0-19 y G0-20 necesitan el snapshot de la release
(`validate_flow_for_release`): un flow aislado no sabe qué páginas existen."""

from collections.abc import Iterator

from agent_core.domain import KnowledgeNode, KnowledgeSnapshot, RespondNode, parse_page_spec
from agent_core.flows.context import Ctx
from agent_core.flows.violations import Violation, clip

SELECTOR_PROPERTY = "path"  # propiedad del `output_schema` del selector cuyo enum son las rutas del scope
INDEX_PAGE = "index.md"
MAX_LISTED = 5


def _knowledge_nodes(ctx: Ctx) -> list[tuple[int, KnowledgeNode]]:
    return [(i, n) for i, n in enumerate(ctx.flow.nodes) if isinstance(n, KnowledgeNode)]


def _generate_responds(ctx: Ctx) -> Iterator[tuple[RespondNode, list[str]]]:
    for node in ctx.flow.nodes:
        if isinstance(node, RespondNode) and node.config.generate is not None:
            yield node, node.config.generate.knowledge_from


def g0_18(ctx: Ctx) -> Iterator[Violation]:
    """Un `respond` con `purpose: customer_answer` solo lee `knowledge_from` de nodos `customer_answer`."""
    by_save_as: dict[str, list[KnowledgeNode]] = {}
    for _, node in _knowledge_nodes(ctx):
        by_save_as.setdefault(node.config.save_as, []).append(node)
    for respond, names in _generate_responds(ctx):
        generate = respond.config.generate
        assert generate is not None
        if generate.purpose != "customer_answer":
            continue
        for position, name in enumerate(names):
            wrong = [n for n in by_save_as.get(name, []) if n.config.purpose != "customer_answer"]
            if wrong:
                yield ctx.v("G0-18", respond.id,
                            f"la respuesta es customer_answer pero {clip(name)!r} viene del nodo "
                            f"{clip(wrong[0].id)} ({wrong[0].config.purpose})",
                            f"/config/generate/knowledge_from/{position}")


def g0_21(ctx: Ctx) -> Iterator[Violation]:
    """Todo `knowledge_from` apunta a un nodo `knowledge` que domina al `respond`: ningún camino desde la
    entrada llega al `respond` sin pasar por él."""
    by_save_as: dict[str, list[KnowledgeNode]] = {}
    for _, node in _knowledge_nodes(ctx):
        by_save_as.setdefault(node.config.save_as, []).append(node)
    for respond, names in _generate_responds(ctx):
        for position, name in enumerate(names):
            where = f"/config/generate/knowledge_from/{position}"
            producers = by_save_as.get(name)
            if not producers:
                yield ctx.v("G0-21", respond.id, f"knowledge_from {clip(name)!r} no es el save_as de ningún "
                            "nodo knowledge", where)
                continue
            cut = frozenset((k.id, result) for k in producers for result in k.next)
            if respond.id in ctx.graph.from_entry(cut):
                yield ctx.v("G0-21", respond.id, f"hay un camino a la respuesta que no pasa por el nodo "
                            f"knowledge de {clip(name)!r}", where)


def g0_17(ctx: Ctx, snapshot: KnowledgeSnapshot | None) -> Iterator[Violation]:
    """Toda página de `knowledge.read` existe en el snapshot de la release (la ruta; el ancla no se ve
    aquí)."""
    known = {page.path for page in snapshot.pages} if snapshot is not None else set()
    for _, node in _knowledge_nodes(ctx):
        if node.config.mode != "read":
            continue
        for position, text in enumerate(node.config.pages):
            path = parse_page_spec(text).path
            if path not in known:
                why = "la release no fija un snapshot de conocimiento" if snapshot is None else (
                    f"la página {clip(path)} no está en el snapshot {snapshot.id}@{snapshot.version}")
                yield ctx.v("G0-17", node.id, why, f"/config/pages/{position}")


def g0_19(ctx: Ctx, snapshot: KnowledgeSnapshot | None) -> Iterator[Violation]:
    """Las páginas fijas de un nodo `customer_answer` son `public` + `approved` en el snapshot."""
    if snapshot is None:
        return
    pages = {page.path: page for page in snapshot.pages}
    for _, node in _knowledge_nodes(ctx):
        if node.config.mode != "read" or node.config.purpose != "customer_answer":
            continue
        for position, text in enumerate(node.config.pages):
            page = pages.get(parse_page_spec(text).path)
            if page is not None and (page.audience != "public" or page.status != "approved"):
                yield ctx.v("G0-19", node.id,
                            f"la página {clip(page.path)} es {page.audience}/{page.status}: un nodo "
                            "customer_answer solo lee páginas public y approved", f"/config/pages/{position}")


def g0_20(ctx: Ctx, snapshot: KnowledgeSnapshot | None) -> Iterator[Violation]:
    """`navigate`: el enum de salida del selector es exactamente el conjunto de rutas del scope."""
    for _, node in _knowledge_nodes(ctx):
        cfg = node.config
        if cfg.mode != "navigate" or cfg.selector is None or cfg.scope is None:
            continue
        model = ctx.model(cfg.selector)
        if model is None:
            continue  # la referencia rota ya es G0-02
        where = "/config/selector"
        if snapshot is None:
            yield ctx.v("G0-20", node.id, "la release no fija un snapshot: no se pueden verificar las rutas "
                        "del scope", where)
            continue
        prefix = cfg.scope + "/"
        routes = {p.path for p in snapshot.pages
                  if p.path.startswith(prefix) and p.path != prefix + INDEX_PAGE}
        properties = model.output_schema.get("properties")
        spec = properties.get(SELECTOR_PROPERTY) if isinstance(properties, dict) else None
        enum = spec.get("enum") if isinstance(spec, dict) else None
        if not isinstance(enum, list) or not all(isinstance(v, str) for v in enum):
            yield ctx.v("G0-20", node.id, f"el selector debe declarar output_schema.properties."
                        f"{SELECTOR_PROPERTY}.enum con strings", where)
            continue
        chosen = {v for v in enum if isinstance(v, str)}
        extra, missing = sorted(chosen - routes), sorted(routes - chosen)
        if len(chosen) != len(enum) or extra or missing:
            yield ctx.v("G0-20", node.id,
                        f"el enum del selector no es el conjunto de rutas de {clip(cfg.scope)}: "
                        f"sobran {_listed(extra)} y faltan {_listed(missing)}", where)


def _listed(items: list[str]) -> str:
    if not items:
        return "ninguna"
    shown = ", ".join(clip(i, 60) for i in items[:MAX_LISTED])
    return shown + (f" y {len(items) - MAX_LISTED} más" if len(items) > MAX_LISTED else "")


def release_rules(ctx: Ctx, snapshot: KnowledgeSnapshot | None) -> Iterator[Violation]:
    yield from g0_17(ctx, snapshot)
    yield from g0_19(ctx, snapshot)
    yield from g0_20(ctx, snapshot)
