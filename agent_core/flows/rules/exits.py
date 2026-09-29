"""G0-06: toda rama de fallo termina en una salida segura (M1 §3.7).

Total: nunca lanza. Una arista a un nodo inexistente o un respond sin `next` no se reportan aquí (G0-03).
Un respond cuya plantilla no resuelve cuenta con lo que sí resolvió (G0-02 ya rechaza el flow).
"""

from collections.abc import Iterator, Mapping
from types import MappingProxyType

from agent_core.domain import CollectNode, EndNode, EscalateNode, Outcome, RespondNode, node_kind
from agent_core.flows.claims import derive_claims
from agent_core.flows.context import Ctx, clip
from agent_core.flows.graph import FlowGraph, flow_mode
from agent_core.flows.violations import Violation

FAILURES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "decide": frozenset({"low_confidence"}),
        "collect": frozenset({"max_attempts"}),
        "tool": frozenset({"error", "timeout", "denied"}),
        "tool_write": frozenset({"denied"}),
        "confirm": frozenset({"max_attempts"}),
        "verify": frozenset({"failed"}),
    }
)
SAFE_ENDS = frozenset({Outcome.abstained, Outcome.clarify_exhausted})


def _safe(
    graph: FlowGraph,
    start: str,
    claims: Mapping[str, frozenset[str]],
    task: bool,
    memo: dict[str, bool],
) -> bool:
    """Sigue la cadena de respond seguros desde `start`. `memo` hace el total lineal en nodos."""
    path: list[str] = []
    seen: set[str] = set()
    current = start
    verdict: bool
    while True:
        if current in memo:
            verdict = memo[current]
            break
        node = graph.nodes[current]
        if isinstance(node, CollectNode | EscalateNode):
            verdict = True
            break
        if isinstance(node, EndNode):
            verdict = node.config.outcome in SAFE_ENDS or (task and node.config.outcome == Outcome.failed)
            break
        if not isinstance(node, RespondNode) or claims.get(current):
            verdict = False
            break
        if current in seen:
            verdict = True  # la vuelta pasa por un respond(await), por G0-04
            break
        seen.add(current)
        path.append(current)
        nxt = node.next.get("next")
        if nxt is None or nxt not in graph.nodes:
            verdict = True  # G0-03 ya lo reporta
            break
        current = nxt
    for ident in path:
        memo[ident] = verdict
    memo[current] = verdict
    return verdict


def g0_06(ctx: Ctx) -> Iterator[Violation]:
    task = flow_mode(ctx.flow) == "task"
    claims = derive_claims(ctx.flow, ctx.reg)
    memo: dict[str, bool] = {}
    for ident in ctx.graph.order:
        node = ctx.graph.nodes[ident]
        for result in sorted(FAILURES.get(node_kind(node) or "", frozenset())):
            dst = node.next.get(result)
            if dst is None or dst not in ctx.graph.nodes:
                continue
            if not _safe(ctx.graph, dst, claims, task, memo):
                yield ctx.v(
                    "G0-06",
                    ident,
                    f"la rama {result} va a {clip(dst)!r}, que no es una salida segura",
                    f"/next/{result}",
                )
