"""G0-23: escrituras `draft` (sin confirm) de una tool `write_draft` (M1 §3.13, ADR 0019).

Total: nunca lanza; una tool que no resuelve no añade ruido (G0-02 ya lo reporta)."""

from collections.abc import Iterator

from agent_core.domain import RiskClass
from agent_core.flows.context import Ctx
from agent_core.flows.graph import Edge, draft_writes, verify_of
from agent_core.flows.rules.writes import verify_entries
from agent_core.flows.violations import Violation, clip

RULE = "G0-23"


def g0_23(ctx: Ctx) -> Iterator[Violation]:
    graph = ctx.graph
    linked: dict[str, list[str]] = {}  # verify enlazado → escrituras draft que lo tienen como ok/uncertain
    for write in sorted(draft_writes(ctx.flow), key=lambda w: w.id):
        ref = write.config.tool
        tool = ctx.tool(ref) if ref is not None else None
        if tool is not None and (tool.risk_class is not RiskClass.write_draft
                                 or tool.readback_by != "idempotency_key"):
            yield ctx.v(RULE, write.id,
                        "la tool de una escritura draft debe ser write_draft con readback_by: "
                        "idempotency_key", "/config/tool")
        verify = verify_of(graph, write)
        if verify is None or verify.config.by != "idempotency_key":
            yield ctx.v(RULE, write.id,
                        "ok y uncertain deben ir directo al mismo verify con by: idempotency_key", "/next")
            continue
        linked.setdefault(verify.id, []).append(write.id)
        cut: frozenset[Edge] = frozenset({(verify.id, "verified")})
        if write.id in graph.reachable([write.id], cut):  # solo se vuelve a W desde `verified`
            yield ctx.v(RULE, write.id,
                        f"{clip(write.id)} se puede repetir sin pasar por verified de {clip(verify.id)}")
    for verify_id, writers in sorted(linked.items()):
        if len(writers) > 1:
            names = ", ".join(clip(w) for w in sorted(writers))
            yield ctx.v(RULE, verify_id, f"el verify lo comparten varias escrituras: {names}")
    yield from verify_entries(ctx, linked)
