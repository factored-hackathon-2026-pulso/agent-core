"""Contrato de `RegistryPort` (spec M0 T-M0-C-*) contra InMemoryRegistry, más las pruebas de seguridad del
doble: solo versiones exactas, entidades publicadas inmutables y sin aliasing con el llamador."""

from collections.abc import Callable

import pytest

from agent_core.domain import (
    AgentSelector,
    EntityRef,
    Flow,
    InvalidRuntimeRef,
    KnowledgeSnapshot,
    RegistryEntity,
    Release,
    Template,
)
from agent_core.ports import RegistryPort
from testing.builders import principal
from testing.fakes.registry import InMemoryRegistry

EXACT_FLOW = {"id": "flujo", "version": "1.0.0", "priority": 1, "nodes": [
    {"id": "t", "type": "tool", "config": {"tool": "buscar@1.0.0", "save_as": "x"},
     "next": {"ok": "fin", "error": "fin", "timeout": "fin", "denied": "fin"}},
    {"id": "fin", "type": "end", "config": {"outcome": "resolved"}}]}
RANGED_FLOW = {**EXACT_FLOW, "id": "flujo-rango", "nodes": [
    {**EXACT_FLOW["nodes"][0], "config": {"tool": "buscar@^1", "save_as": "x"}}, EXACT_FLOW["nodes"][1]]}

KNOWLEDGE = KnowledgeSnapshot.model_validate({"id": "kb-base", "version": "1.0.0", "pages": [
    {"path": "faq/disputas", "hash": "a" * 64, "audience": "public", "status": "approved",
     "approved_by": "aprobador-1", "lang": "es"}]})


def _release(release_id: str = "rel-1", **over: object) -> Release:
    return Release.model_validate({"id": release_id, "status": "active",
                                   "language_detection": "lang@1.0.0", **over})


def check_get_exact_entity(registry: RegistryPort) -> None:
    flow = registry.get(EntityRef.parse("flujo@1.0.0"), Flow)
    assert flow.id == "flujo"
    assert registry.get(EntityRef.parse("t/saludo@1.0.0"), Template).locales["es"] == "Hola"


def check_get_with_non_exact_content_raises(registry: RegistryPort) -> None:
    with pytest.raises(InvalidRuntimeRef):
        registry.get(EntityRef.parse("flujo-rango@1.0.0"), Flow)


def check_resolve_release_by_alias_and_version(registry: RegistryPort) -> None:
    assert registry.resolve_release(AgentSelector.parse("atencion"), principal()).id == "rel-1"
    assert registry.resolve_release(AgentSelector.parse("atencion@1.0.0"), principal()).id == "rel-1"


def check_get_serves_only_the_exact_version(registry: RegistryPort) -> None:
    with pytest.raises(KeyError):
        registry.get(EntityRef.parse("flujo@1.0.1"), Flow)
    with pytest.raises(KeyError):
        registry.get(EntityRef.parse("flujo@2.0.0"), Flow)


def check_get_with_wrong_kind_fails_instead_of_returning_other_type(registry: RegistryPort) -> None:
    with pytest.raises(KeyError):
        registry.get(EntityRef.parse("flujo@1.0.0"), Template)


def check_unknown_selector_and_release_fail_closed(registry: RegistryPort) -> None:
    with pytest.raises(KeyError):
        registry.resolve_release(AgentSelector.parse("desconocido"), principal())
    with pytest.raises(KeyError):
        registry.resolve_release(AgentSelector.parse("atencion@9.9.9"), principal())
    with pytest.raises(KeyError):
        registry.release_status("rel-x")


def check_release_status_is_active(registry: RegistryPort) -> None:
    assert registry.release_status("rel-1") == "active"


def check_returned_entities_are_copies(registry: RegistryPort) -> None:
    tpl = registry.get(EntityRef.parse("t/saludo@1.0.0"), Template)
    tpl.locales["es"] = "Hackeado"
    flow = registry.get(EntityRef.parse("flujo@1.0.0"), Flow)
    flow.nodes.clear()
    assert registry.get(EntityRef.parse("t/saludo@1.0.0"), Template).locales == {"es": "Hola"}
    assert len(registry.get(EntityRef.parse("flujo@1.0.0"), Flow).nodes) == 2


def check_returned_release_is_a_copy(registry: RegistryPort) -> None:
    rel = registry.resolve_release(AgentSelector.parse("atencion"), principal())
    rel.interrupts.append(rel.interrupts)  # type: ignore[arg-type]
    assert registry.resolve_release(AgentSelector.parse("atencion"), principal()).interrupts == []


def check_get_knowledge_snapshot(registry: RegistryPort) -> None:
    snapshot = registry.get(EntityRef.parse("kb-base@1.0.0"), KnowledgeSnapshot)
    assert [page.path for page in snapshot.pages] == ["faq/disputas"]


def check_get_unknown_version_raises_key_error(registry: RegistryPort) -> None:
    with pytest.raises(KeyError):
        registry.get(EntityRef.parse("flujo@9.9.9"), Flow)


# Spec del registry §7.1: toda implementación de `RegistryPort` del paquete (memoria, `SnapshotRegistry` y
# `PostgresRegistry`) pasa ESTA lista completa. Añadir una comprobación aquí la añade a las tres.
CHECKS = (
    check_get_exact_entity,
    check_get_with_non_exact_content_raises,
    check_resolve_release_by_alias_and_version,
    check_get_serves_only_the_exact_version,
    check_get_with_wrong_kind_fails_instead_of_returning_other_type,
    check_unknown_selector_and_release_fail_closed,
    check_release_status_is_active,
    check_returned_entities_are_copies,
    check_returned_release_is_a_copy,
    check_get_knowledge_snapshot,
    check_get_unknown_version_raises_key_error,
)


def _entities() -> list[RegistryEntity]:
    return [Flow.model_validate(EXACT_FLOW), Flow.model_validate(RANGED_FLOW),
            Template(id="t/saludo", version="1.0.0", locales={"es": "Hola"}), KNOWLEDGE]


@pytest.fixture(params=["in_memory", "snapshot"])
def registry(request: pytest.FixtureRequest) -> RegistryPort:
    if request.param == "snapshot":
        from agent_core.registry.snapshot import SnapshotRegistry  # T-REG-17: SnapshotRegistry (spec §5.3)
        release = _release("rel-1", entities={"agent": {"atencion": "1.0.0"}})
        return SnapshotRegistry(release, _entities())
    reg = InMemoryRegistry()
    reg.add(*_entities())
    reg.add_release(_release("rel-1", entities={"agent": {"atencion": "1.0.0"}}), agent_id="atencion")
    reg.add_release(_release("rel-1", entities={"agent": {"atencion": "1.0.0"}}), agent_id="atencion",
                    alias=None, version="1.0.0")
    return reg


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_registry_port_contract(registry: RegistryPort, check: Callable[[RegistryPort], None]) -> None:
    check(registry)  # T-REG-17


def test_revoked_release_status() -> None:
    reg = InMemoryRegistry()
    reg.add_release(_release(), agent_id="a")
    assert reg.release_status("rel-1") == "active"
    reg.revoke("rel-1")
    assert reg.release_status("rel-1") == "revoked"
    assert reg.resolve_release(AgentSelector.parse("a"), principal()).status == "revoked"


# --- solo versiones exactas, sin resolución ---------------------------------------------------------------




def test_alias_and_version_are_separate_namespaces() -> None:
    reg = InMemoryRegistry()
    reg.add_release(_release("rel-1"), agent_id="a", alias="prod")
    reg.add_release(_release("rel-2"), agent_id="a", alias=None, version="1.0.0")
    assert reg.resolve_release(AgentSelector.parse("a@prod"), principal()).id == "rel-1"
    assert reg.resolve_release(AgentSelector.parse("a@1.0.0"), principal()).id == "rel-2"


def test_add_release_needs_alias_or_version_and_valid_format() -> None:
    reg = InMemoryRegistry()
    with pytest.raises(ValueError):
        reg.add_release(_release(), agent_id="a", alias=None)
    with pytest.raises(ValueError):
        reg.add_release(_release(), agent_id="a", alias=None, version="^1")


# --- inmutabilidad de lo publicado -------------------------------------------------------------------------

def test_republishing_same_content_is_idempotent_but_different_content_is_rejected() -> None:
    reg = InMemoryRegistry()
    reg.add(Template(id="t/x", version="1.0.0", locales={"es": "Hola"}))
    reg.add(Template(id="t/x", version="1.0.0", locales={"es": "Hola"}))
    with pytest.raises(ValueError):
        reg.add(Template(id="t/x", version="1.0.0", locales={"es": "Otro"}))
    assert reg.get(EntityRef.parse("t/x@1.0.0"), Template).locales == {"es": "Hola"}


def test_rejected_batch_publishes_nothing() -> None:
    reg = InMemoryRegistry()
    reg.add(Template(id="t/x", version="1.0.0", locales={"es": "Hola"}))
    with pytest.raises(ValueError):
        reg.add(Template(id="t/nuevo", version="1.0.0", locales={"es": "N"}),
                Template(id="t/x", version="1.0.0", locales={"es": "Otro"}))
    with pytest.raises(KeyError):
        reg.get(EntityRef.parse("t/nuevo@1.0.0"), Template)


def test_release_id_and_version_pin_are_immutable_but_alias_can_move() -> None:
    reg = InMemoryRegistry()
    reg.add_release(_release("rel-1"), agent_id="a", alias=None, version="1.0.0")
    with pytest.raises(ValueError):
        reg.add_release(_release("rel-2"), agent_id="a", alias=None, version="1.0.0")
    with pytest.raises(ValueError):
        reg.add_release(_release("rel-1", max_input_chars=10), agent_id="a", alias="prod")
    reg.add_release(_release("rel-3"), agent_id="a", alias="prod")
    reg.add_release(_release("rel-4"), agent_id="a", alias="prod")
    assert reg.resolve_release(AgentSelector.parse("a"), principal()).id == "rel-4"


def test_revoked_release_cannot_be_reactivated_by_republishing() -> None:
    reg = InMemoryRegistry()
    reg.add_release(_release(), agent_id="a")
    reg.revoke("rel-1")
    with pytest.raises(ValueError):
        reg.add_release(_release(), agent_id="a")
    assert reg.release_status("rel-1") == "revoked"


def test_revoke_unknown_release_fails() -> None:
    with pytest.raises(KeyError):
        InMemoryRegistry().revoke("rel-x")


# --- sin aliasing: los llamadores no pueden mutar el registro --------------------------------------------



def test_source_object_mutation_after_add_does_not_alter_registry() -> None:
    reg = InMemoryRegistry()
    tpl = Template(id="t/x", version="1.0.0", locales={"es": "Hola"})
    reg.add(tpl)
    tpl.locales["es"] = "Hackeado"
    assert reg.get(EntityRef.parse("t/x@1.0.0"), Template).locales == {"es": "Hola"}


# --- snapshot de conocimiento (registry, ADR 0017) ----------------------------------------------------------

def check_get_knowledge_snapshot(registry: RegistryPort) -> None:
    snapshot = registry.get(EntityRef.parse("kb-base@1.0.0"), KnowledgeSnapshot)
    assert [page.path for page in snapshot.pages] == ["faq/disputas"]



def test_release_carries_its_exact_knowledge_snapshot() -> None:
    reg = InMemoryRegistry()
    reg.add(KNOWLEDGE)
    reg.add_release(_release(knowledge_snapshot="kb-base@1.0.0"), agent_id="a")
    release = reg.resolve_release(AgentSelector.parse("a"), principal())
    assert release.knowledge_snapshot is not None
    assert reg.get(release.knowledge_snapshot, KnowledgeSnapshot).id == "kb-base"


def test_knowledge_snapshot_is_immutable_once_published() -> None:
    reg = InMemoryRegistry()
    reg.add(KNOWLEDGE)
    reg.add(KNOWLEDGE)  # mismo contenido: idempotente
    other = KnowledgeSnapshot.model_validate({"id": "kb-base", "version": "1.0.0", "pages": []})
    with pytest.raises(ValueError):
        reg.add(other)
