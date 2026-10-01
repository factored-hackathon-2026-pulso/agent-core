"""Cableado de M12 en la raíz de composición (m12 §2): la fuente entra por `EngineDeps` y el servicio llega
al `StepContext` de M2; sin fuente, no hay servicio (un nodo `knowledge` es entonces un error de cableado)."""

from agent_core.knowledge import KnowledgeService
from testing.builders import run_state
from testing.engine_world import EngineWorld
from testing.fakes.knowledge import InMemoryKnowledgeSource
from tests.m12.helpers import standard_records


def _open(w: EngineWorld):  # type: ignore[no-untyped-def]
    state = run_state(release=w.release.id, agent="atencion@1.0.0")
    return w.runtimes.open(state, w.principal, None)


def test_with_a_source_open_injects_the_knowledge_service() -> None:
    w = EngineWorld(knowledge=InMemoryKnowledgeSource(standard_records()))
    assert isinstance(_open(w).step.knowledge, KnowledgeService)


def test_without_a_source_there_is_no_knowledge_service() -> None:
    assert _open(EngineWorld()).step.knowledge is None


def test_the_service_asks_the_authz_of_the_deployment_for_the_view() -> None:
    w = EngineWorld(knowledge=InMemoryKnowledgeSource(standard_records()))
    service = _open(w).step.knowledge
    assert service is not None
    assert w.deps.authz.knowledge_view(w.principal, "customer_answer").audiences == frozenset({"public"})
