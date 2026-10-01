"""G0-02 (referencias), G0-03 (estructura del grafo) y G0-04 (ciclos sin espera)."""

from collections.abc import Iterator, Mapping, Sequence

from agent_core.domain import RESULTS, DecideNode, DecisionModelDef, KnowledgeNode, node_kind
from agent_core.flows.context import Ctx
from agent_core.flows.graph import is_waiting
from agent_core.flows.refs import flow_ref_sites, pointer_str
from agent_core.flows.violations import Violation, clip, pointer_segment

MAX_LISTED_MEMBERS = 10
CHOICE_RESULTS = frozenset({"chosen", "none", "low_confidence"})  # decide with runtime choices (ADR 0021)


def g0_02(ctx: Ctx) -> Iterator[Violation]:
    for site in flow_ref_sites(ctx.flow):
        if ctx.reg.resolve(site.kind, site.ref) is None:
            yield Violation(rule="G0-02", node_id=site.node_id, path=pointer_str(site.pointer),
                            message=f"{site.kind.value} {clip(str(site.ref))} no existe en el registro")


def decide_enum(model: DecisionModelDef, field: str) -> list[str] | None:
    """Valores del enum de `branch_on` en `output_schema.properties`, o None si no es válido (G0-03e)."""
    properties = model.output_schema.get("properties")
    spec = properties.get(field) if isinstance(properties, dict) else None
    enum = spec.get("enum") if isinstance(spec, dict) else None
    if not isinstance(enum, list) or not enum:
        return None
    values = [v for v in enum if isinstance(v, str)]
    if len(values) != len(enum) or len(set(values)) != len(values) or "low_confidence" in values:
        return None
    return values


def g0_03(ctx: Ctx) -> Iterator[Violation]:
    graph = ctx.graph
    for ident in graph.order:
        node = graph.nodes[ident]
        for key, dst in sorted(node.next.items()):
            if dst not in graph.nodes:
                yield ctx.v("G0-03", ident, f"next.{clip(key)} apunta a {clip(dst)!r}, que no existe",
                            f"/next/{pointer_segment(key)}")
        expected: frozenset[str] | None = RESULTS.get(node_kind(node) or "", frozenset())
        if isinstance(node, KnowledgeNode) and node.config.mode == "read":
            expected = frozenset({"ok", "not_found", "denied"})  # `low_confidence` es solo de navigate
        if isinstance(node, DecideNode) and node.config.choices_from is not None:
            if node.config.branch_on != "choice":
                yield ctx.v("G0-03", ident, "un decide con choices_from ramifica por 'choice'",
                            "/config/branch_on")
            expected = CHOICE_RESULTS
        elif isinstance(node, DecideNode):
            model = ctx.model(node.config.model)
            if model is None:
                expected = None  # la referencia rota ya es G0-02
            else:
                enum = decide_enum(model, node.config.branch_on)
                if enum is None:
                    yield ctx.v("G0-03", ident,
                                f"branch_on {clip(node.config.branch_on)!r} debe ser un enum de strings de "
                                "output_schema.properties, sin repetidos ni 'low_confidence'",
                                "/config/branch_on")
                    expected = None
                else:
                    expected = frozenset(enum) | {"low_confidence"}
        if expected is None:
            continue
        for key in sorted(set(node.next) - expected):
            yield ctx.v("G0-03", ident, f"resultado desconocido {clip(key)!r} para un nodo {node.type}",
                        f"/next/{pointer_segment(key)}")
        for key in sorted(expected - set(node.next)):
            yield ctx.v("G0-03", ident, f"el resultado {clip(key)!r} no tiene next", "/next")
    reachable = graph.from_entry()
    for ident in graph.order:
        if ident not in reachable:
            yield ctx.v("G0-03", ident, "nodo inalcanzable desde la entrada")


def _sccs(order: Sequence[str], adj: Mapping[str, list[str]]) -> list[list[str]]:
    """Componentes fuertemente conexas (Tarjan iterativo: pila explícita, sin recursión)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []
    counter = 0
    for root in order:
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            current, position = work.pop()
            if position == 0:
                index[current] = low[current] = counter
                counter += 1
                stack.append(current)
                on_stack.add(current)
            descended = False
            successors = adj[current]
            for j in range(position, len(successors)):
                nxt = successors[j]
                if nxt not in index:
                    work.append((current, j + 1))
                    work.append((nxt, 0))
                    descended = True
                    break
                if nxt in on_stack:
                    low[current] = min(low[current], index[nxt])
            if descended:
                continue
            if low[current] == index[current]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == current:
                        break
                components.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[current])
    return components


def g0_04(ctx: Ctx) -> Iterator[Violation]:
    """Sin los nodos que esperan, el grafo debe ser acíclico (auto-bucles incluidos)."""
    graph = ctx.graph
    keep = [ident for ident in graph.order if not is_waiting(graph.nodes[ident])]
    kept = set(keep)
    adj = {ident: [dst for _, dst in graph.succ[ident] if dst in kept] for ident in keep}
    for component in _sccs(keep, adj):
        if len(component) > 1 or component[0] in adj[component[0]]:
            members = sorted(component)
            listed = ", ".join(clip(m) for m in members[:MAX_LISTED_MEMBERS])
            extra = len(members) - MAX_LISTED_MEMBERS
            tail = f" y {extra} más" if extra > 0 else ""
            yield ctx.v("G0-04", members[0], f"ciclo sin nodo que espere al principal: {listed}{tail}")
