"""Arnés de M12: fuente, servicio y contexto sintéticos. Solo datos sintéticos."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from agent_core.domain import (
    KnowledgeNode,
    KnowledgeView,
    PageMeta,
    PageRecord,
    Purpose,
    Release,
    RunState,
)
from agent_core.knowledge import KnowledgeContext, KnowledgeService
from agent_core.views import TokenVault, ViewService
from testing.builders import NOW, advisor_with_delegation, principal, run_state
from testing.fakes.authz import TableAuthz
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.knowledge import InMemoryKnowledgeSource

SNAPSHOT = "kb-base@1.0.0"

CARGOS = """# Cargos no reconocidos

Puedes disputar un cargo que no reconoces.

## Plazo de respuesta {#plazos}

Respondemos tu disputa en 15 días hábiles.

## Qué necesitas {#requisitos}

Ten a la mano la fecha y el monto del cargo.
"""


def meta(path: str = "faq/cargos.md", **over: Any) -> PageMeta:
    base: dict[str, Any] = {
        "path": path, "anchor": None, "snapshot": SNAPSHOT, "type": "faq", "audience": "public",
        "status": "approved", "approved_by": "revisor-demo", "lang": "es"}
    merged = base | over
    if merged["status"] == "draft":
        merged["approved_by"] = None
    return PageMeta.model_validate(merged)


def record(path: str = "faq/cargos.md", content: str = CARGOS, **over: Any) -> PageRecord:
    return PageRecord(meta=meta(path, **over), content=content)


def standard_records() -> list[PageRecord]:
    return [
        record(),
        record("faq/cargos.pt.md", "# Cobranças\n\n## Prazo {#plazos}\n\nRespondemos em 15 dias úteis.\n",
               lang="pt", translation_of="faq/cargos.md"),
        record("faq/borrador.md", "# Borrador\n\nSin revisar.\n", status="draft"),
        record("faq/vencida.md", "# Vencida\n\nYa no aplica.\n", valid_to="2026-06-30"),
        record("faq/futura.md", "# Futura\n\nAún no aplica.\n", valid_from="2027-01-01"),
        record("faq/externa.md", "# Externa\n\nTexto de fuera.\n",
               source_refs=["https://ejemplo.test/articulo-sintetico"]),
        record("proc/reversos.md", "# Reversos\n\nSolo asesores.\n", audience="internal", type="procedure"),
        record("guia/agente.md", "# Guía\n\nSolo el agente.\n", audience="agent_only", type="guidance"),
    ]


class LeakySource(InMemoryKnowledgeSource):
    """Un servicio defectuoso: ignora la vista y devuelve todo lo que existe (el doble filtro lo atrapa)."""

    def read(self, path: str, snapshot: str, view: KnowledgeView) -> PageRecord | None:
        return super().read(path, snapshot, ALL_VIEW)

    def index(self, snapshot: str, view: KnowledgeView) -> list[PageMeta]:
        return super().index(snapshot, ALL_VIEW)


ALL_VIEW = KnowledgeView(audiences=frozenset({"public", "internal", "agent_only"}), approved_only=False)


class DownSource(InMemoryKnowledgeSource):
    def read(self, path: str, snapshot: str, view: KnowledgeView) -> PageRecord | None:
        raise ConnectionError("servicio de conocimiento caído")

    def index(self, snapshot: str, view: KnowledgeView) -> list[PageMeta]:
        raise ConnectionError("servicio de conocimiento caído")


class CountingSource(InMemoryKnowledgeSource):
    def __init__(self, records: Iterable[PageRecord] = ()) -> None:
        super().__init__(records)
        self.reads = 0

    def read(self, path: str, snapshot: str, view: KnowledgeView) -> PageRecord | None:
        self.reads += 1
        return super().read(path, snapshot, view)


def node(*pages: str, purpose: Purpose = "customer_answer", save_as: str = "kb",
         **config: Any) -> KnowledgeNode:
    cfg = {"mode": "read", "pages": list(pages), "purpose": purpose, "save_as": save_as} | config
    if cfg["mode"] == "navigate":
        cfg["pages"] = []
    return KnowledgeNode.model_validate({
        "id": "saber", "type": "knowledge", "config": cfg,
        "next": {"ok": "fin", "not_found": "fin", "denied": "fin"}})


def release(snapshot: str | None = SNAPSHOT) -> Release:
    return Release.model_validate({
        "id": "rel-2026-09-28", "status": "active", "entities": {}, "language_detection": "lang@1.0.0",
        "knowledge_snapshot": snapshot})


@dataclass
class World:
    source: InMemoryKnowledgeSource = field(default_factory=lambda: InMemoryKnowledgeSource(standard_records()))
    snapshot: str | None = SNAPSHOT

    def __post_init__(self) -> None:
        self.clock = FakeClock()
        self.ids = FakeIds()
        keys = FakeKeyProvider.default()
        self.authz = TableAuthz()
        self.views = ViewService(keys, self.authz, self.clock)
        self.vault = TokenVault("run-0001", keys, self.ids)
        self.service = KnowledgeService(self.source, self.authz)

    def ctx(self) -> KnowledgeContext:
        return KnowledgeContext(release=release(self.snapshot), clock=self.clock, ids=self.ids,
                                views=self.views, vault=self.vault, turn_id="turn-0001")

    def read(self, knowledge: KnowledgeNode, state: RunState | None = None,
             ) -> tuple[RunState, str, list[Any]]:
        return self.service.read(knowledge, state or run_state(), self.ctx())


def advisor_state(**over: Any) -> RunState:
    advisor, obo = advisor_with_delegation()
    return run_state(principal=advisor, on_behalf_of=obo, **over)


__all__ = ["CARGOS", "NOW", "SNAPSHOT", "CountingSource", "DownSource", "LeakySource", "World",
           "advisor_state", "meta", "node", "principal", "record", "release", "run_state", "standard_records"]
