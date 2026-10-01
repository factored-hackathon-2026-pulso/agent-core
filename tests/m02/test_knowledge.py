"""Handler del nodo `knowledge` (M2 §3.3, m12 §3.1): delega en M12 y sigue por `ok`, `not_found` o
`denied`."""

from typing import Any

import pytest

from agent_core.domain import IllegalTransition, KnowledgeRead, page_ref
from agent_core.interpreter import Stop
from agent_core.knowledge import KnowledgeService
from testing.fakes.authz import TableAuthz
from testing.fakes.knowledge import InMemoryKnowledgeSource
from tests.m02.harness import RELEASE_ID, World, flow
from tests.m12.helpers import SNAPSHOT, LeakySource, record, standard_records

TAIL = [
    {"id": "dicho", "type": "respond", "config": {"template_ref": "t/ok@1.0.0"}, "next": {"next": "fin"}},
    {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "sin", "type": "escalate", "config": {"reason_code": "low_confidence"}},
    {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}},
]


def _node(*pages: str, purpose: str = "customer_answer", **cfg: Any) -> dict[str, Any]:
    config = {"mode": "read", "pages": list(pages), "purpose": purpose, "save_as": "kb"} | cfg
    return {"id": "saber", "type": "knowledge", "config": config,
            "next": {"ok": "dicho", "not_found": "sin", "denied": "esc"}}


def _world(source: InMemoryKnowledgeSource | None = None,
           snapshot: str | None = SNAPSHOT) -> tuple[World, Any]:
    from agent_core.domain import Template

    w = World()
    w.add(Template.model_validate(
        {"id": "t/ok", "version": "1.0.0", "locales": {"es": "Listo.", "pt": "Pronto."}}))
    service = KnowledgeService(source or InMemoryKnowledgeSource(standard_records()), TableAuthz())
    return w, service


def _release(w: World, snapshot: str | None) -> Any:
    release = w.release()
    if snapshot is None:
        return release
    return release.model_copy(update={"knowledge_snapshot": snapshot})


def _step(w: World, service: Any, node: dict[str, Any], *, snapshot: str | None = SNAPSHOT,
          **over: Any) -> Any:
    state = w.state(flow(node, *TAIL))
    return w.step(state, release=_release(w, snapshot), knowledge=service, **over)


def test_ok_delivers_the_pages_and_follows_the_next_node() -> None:
    w, service = _world()
    out = _step(w, service, _node("faq/cargos.md#plazos"))
    assert out.stop is Stop.terminal and out.end_outcome is not None  # siguió por `ok` hasta el `end`
    assert [m.text for m in out.messages] == ["Listo."]
    (page,) = out.state.pages["kb"]
    assert page.ref == page_ref("faq/cargos.md", SNAPSHOT, "plazos") and "15 días" in page.content_model
    assert out.state.facts == {}  # una página no entra como hecho


def test_the_knowledge_read_event_is_emitted_after_node_entered() -> None:
    w, service = _world()
    out = _step(w, service, _node("faq/cargos.md#plazos"))
    kinds = [e.type for e in out.events]
    assert kinds.index("node_entered") < kinds.index("knowledge_read")
    event = next(e for e in out.events if isinstance(e, KnowledgeRead))
    assert event.payload.result == "ok" and event.payload.node_id == "saber"
    assert event.turn_id == "turn-0001" and event.release == RELEASE_ID


def test_not_found_follows_the_not_found_edge() -> None:
    w, service = _world()
    out = _step(w, service, _node("faq/no-existe.md"))
    assert out.stop is Stop.terminal and out.escalation is not None
    assert out.escalation.reason_code == "low_confidence"  # el nodo `sin`
    assert "kb" not in out.state.pages


def test_denied_follows_the_denied_edge() -> None:
    w, service = _world(LeakySource(standard_records()))
    out = _step(w, service, _node("proc/reversos.md"))
    assert out.escalation is not None and out.escalation.reason_code == "tool_failure"  # el nodo `esc`
    assert "kb" not in out.state.pages


def test_a_source_that_is_down_leaves_by_not_found() -> None:
    class Down(InMemoryKnowledgeSource):
        def read(self, *args: Any) -> Any:
            raise ConnectionError("caído")

    w, service = _world(Down())
    out = _step(w, service, _node("faq/cargos.md"))
    assert out.escalation is not None and out.escalation.reason_code == "low_confidence"
    event = next(e for e in out.events if isinstance(e, KnowledgeRead))
    assert event.payload.reason == "source_unavailable"


def test_a_navigate_node_is_closed_at_runtime() -> None:
    w, service = _world()
    nav = _node(mode="navigate", scope="faq", selector="selector@1.0.0", pages=[])
    nav["config"].pop("pages", None)
    nav["next"]["low_confidence"] = "sin"
    out = _step(w, service, nav)
    assert out.escalation is not None and out.escalation.reason_code == "low_confidence"
    event = next(e for e in out.events if isinstance(e, KnowledgeRead))
    assert event.payload.reason == "navigate_unavailable"


def test_without_a_knowledge_service_the_node_is_a_wiring_error() -> None:
    w, _ = _world()
    state = w.state(flow(_node("faq/cargos.md"), *TAIL))
    with pytest.raises(IllegalTransition):
        w.step(state)


def test_the_node_is_registered_in_the_handlers() -> None:
    from agent_core.interpreter.handlers import HANDLERS

    assert "knowledge" in HANDLERS


def test_degraded_mode_still_reads_fixed_pages() -> None:
    """Leer páginas fijas no usa el modelo: el modo degradado no lo impide."""
    w, service = _world()
    out = _step(w, service, _node("faq/cargos.md#plazos"), degraded=True)
    assert "kb" in out.state.pages


def test_a_page_added_to_the_source_is_visible_without_touching_the_handler() -> None:
    source = InMemoryKnowledgeSource([*standard_records(), record("faq/nueva.md", "# Nueva\n\nTexto.\n")])
    w, service = _world(source)
    out = _step(w, service, _node("faq/nueva.md"))
    assert out.state.pages["kb"][0].meta.path == "faq/nueva.md"
