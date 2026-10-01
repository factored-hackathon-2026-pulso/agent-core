"""G0-05: invariante de escritura (M1 §3.5).

Total: nunca lanza; un prerrequisito sin resolver (tool inexistente) no añade ruido (G0-02 ya lo reporta).

Endurecimiento sobre §3.5.7 (el spec se enmendará): el verify V de una escritura W no puede ser el nodo de
entrada y sus únicas aristas entrantes son (W, ok) y (W, uncertain). Así ningún camino llega a
`V.verified` sin haber ejecutado W (tras su confirm.yes).
"""

from collections.abc import Iterator

from agent_core.domain import ConfirmNode, RiskClass, ToolNode, VerifyNode, WriteToolNode
from agent_core.flows.claims import derive_claims
from agent_core.flows.context import Ctx
from agent_core.flows.graph import Edge, draft_writes, verify_of, writes_by_action, writes_by_confirm
from agent_core.flows.violations import Violation, clip, pointer_segment

RULE = "G0-05"


def _node_conditions(ctx: Ctx) -> Iterator[Violation]:
    """§3.5.1 y §3.5.2: qué tools puede invocar un nodo tool sin action_from y qué lee un verify."""
    for node in ctx.flow.nodes:
        if isinstance(node, ToolNode):
            tool = ctx.tool(node.config.tool)
            if tool is not None and tool.is_write:
                yield ctx.v(RULE, node.id,
                            f"la tool de escritura {clip(str(node.config.tool))} solo se invoca "
                            "con action_from o con draft: true",
                            "/config/tool")
        elif isinstance(node, VerifyNode):
            readback = ctx.tool(node.config.readback)
            if readback is not None and readback.risk_class != RiskClass.read:
                yield ctx.v(RULE, node.id,
                            f"el readback {clip(str(node.config.readback))} debe ser de clase read",
                            "/config/readback")


def _write_conditions(ctx: Ctx, writes: dict[str, list[WriteToolNode]]) -> Iterator[Violation]:
    """§3.5.3 a §3.5.7, por confirm."""
    graph = ctx.graph
    verify_writers: dict[str, list[str]] = {}
    linked: dict[str, list[str]] = {}  # verify enlazado → escrituras que lo tienen como ok/uncertain
    for confirm_id, group in sorted(writes.items()):
        group = sorted(group, key=lambda w: w.id)
        confirm = graph.nodes.get(confirm_id)
        if not isinstance(confirm, ConfirmNode):
            for write in group:
                yield ctx.v(RULE, write.id,
                            f"action_from apunta a {clip(confirm_id)!r}, que no es un confirm del flow",
                            "/config/action_from")
            continue
        tool = ctx.tool(confirm.config.action.tool)
        if tool is not None and (not tool.is_write or tool.readback_by != "idempotency_key"):
            yield ctx.v(RULE, confirm.id,
                        "la tool del confirm debe ser de escritura con readback_by: idempotency_key",
                        "/config/action/tool")
        if len(group) > 1:
            names = ", ".join(clip(w.id) for w in group)
            yield ctx.v(RULE, confirm.id, f"el confirm tiene más de una escritura: {names}")
        without_yes: frozenset[Edge] = frozenset({(confirm_id, "yes")})
        from_entry = graph.from_entry(without_yes)  # una vez por confirm
        for write in group:
            if write.id in from_entry or write.id in graph.reachable([write.id], without_yes):
                yield ctx.v(RULE, write.id,
                            f"hay caminos a {clip(write.id)} que no pasan por {clip(confirm_id)}.yes")
            verify = verify_of(graph, write)
            if verify is not None:
                linked.setdefault(verify.id, []).append(write.id)
            if verify is None or verify.config.by != "idempotency_key":
                yield ctx.v(RULE, write.id,
                            "ok y uncertain deben ir directo al mismo verify con by: "
                            "idempotency_key", "/next")
            else:
                verify_writers.setdefault(verify.id, []).append(write.id)
    for verify_id, writers in sorted(verify_writers.items()):
        if len(writers) > 1:
            names = ", ".join(clip(w) for w in sorted(writers))
            yield ctx.v(RULE, verify_id, f"el verify lo comparten varias escrituras: {names}")
    yield from verify_entries(ctx, linked)


def verify_entries(ctx: Ctx, linked: dict[str, list[str]]) -> Iterator[Violation]:
    """Endurecimiento de §3.5.7: V solo se alcanza desde (W, ok) y (W, uncertain), y no es la entrada."""
    graph = ctx.graph
    incoming: dict[str, list[tuple[str, str]]] = {}
    for src in graph.order:
        for result, dst in graph.succ.get(src, ()):
            if dst in linked:
                incoming.setdefault(dst, []).append((src, result))
    for verify_id, writers in sorted(linked.items()):
        if graph.entry == verify_id:
            yield ctx.v(RULE, verify_id, f"el verify {clip(verify_id)} no puede ser el nodo de entrada")
        for src, result in incoming.get(verify_id, []):
            if src in writers and result in ("ok", "uncertain"):
                continue
            yield ctx.v(RULE, src,
                        f"el verify {clip(verify_id)} solo se alcanza desde ok y uncertain de su escritura; "
                        f"sobra next.{clip(result)}", f"/next/{pointer_segment(result)}")


def _claim_conditions(ctx: Ctx, writes: dict[str, list[WriteToolNode]]) -> Iterator[Violation]:
    """§3.5.8: todo camino a un lector que reclama X pasa por `verified` del verify de X."""
    graph = ctx.graph
    action_ids = {n.id for n in ctx.flow.nodes if isinstance(n, ConfirmNode)} | {
        w.id for w in draft_writes(ctx.flow)}
    claims = derive_claims(ctx.flow, ctx.reg)
    readers_by_action: dict[str, list[str]] = {}
    for reader_id, claimed in sorted(claims.items()):
        for action in sorted(claimed & action_ids):
            readers_by_action.setdefault(action, []).append(reader_id)
    for action, readers in sorted(readers_by_action.items()):
        verifies = {v.id: v for w in writes.get(action, []) if (v := verify_of(graph, w)) is not None}
        if not verifies:
            for reader_id in readers:
                yield ctx.v(RULE, reader_id,
                            f"reclama {clip(action)}, que no tiene una escritura verificable")
            continue
        for verify_id in sorted(verifies):  # un solo verify salvo que 5 ya esté violada
            cut: frozenset[Edge] = frozenset({(verify_id, "verified")})
            exposed = graph.from_entry(cut) | graph.reachable([action], cut)
            for reader_id in readers:
                if reader_id in exposed:
                    yield ctx.v(RULE, reader_id,
                                f"reclama {clip(action)} en un camino que no pasa por verified "
                                f"de {clip(verify_id)}")


def g0_05(ctx: Ctx) -> Iterator[Violation]:
    writes = writes_by_confirm(ctx.flow)
    yield from _node_conditions(ctx)
    yield from _write_conditions(ctx, writes)
    yield from _claim_conditions(ctx, writes_by_action(ctx.flow))
