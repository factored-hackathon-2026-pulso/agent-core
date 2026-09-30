"""Registry sobre Postgres real (spec §13). Requiere docker compose up -d postgres."""

from datetime import timedelta
from typing import Any

import psycopg
import pytest

from agent_core.domain import AgentSelector, EntityRef, Flow, Release, Template
from agent_core.registry.errors import IntegrityError, RegistryError, RegistryErrorCode
from agent_core.registry.models import Origin
from agent_core.registry.postgres.runtime import PostgresRegistry
from agent_core.registry.postgres.store import PgRegistryStore
from agent_core.registry.service import RegistryService
from testing.builders import principal
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.contracts.test_registry_contract import (
    EXACT_FLOW,
    KNOWLEDGE,
    RANGED_FLOW,
    check_get_exact_entity,
    check_get_knowledge_snapshot,
    check_get_with_non_exact_content_raises,
    check_resolve_release_by_alias_and_version,
)
from tests.registry.helpers import AGENT, REGISTRY_DEMO, human, prompt_draft
from tests.registry.service_world import SUITE, FakeEvaluator

pytestmark = pytest.mark.integration
ANA = human()


def _service(store: PgRegistryStore) -> RegistryService:
    return RegistryService(store, FakeEvaluator(), FakeClock(), FakeIds())


def _publish(service: RegistryService, key: str = "k", version: str = "1.1.0") -> str:
    p = service.create_proposal(ANA, AGENT, Origin.manual, "t")
    service.put_draft(ANA, p.proposal_id, [prompt_draft(version=version, text=f"Texto {version}."), SUITE],
                      expected_rev=0)
    service.freeze(ANA, p.proposal_id)
    service.evaluate(ANA, p.proposal_id, "disputas-suite")
    h = service.get_proposal(p.proposal_id).proposal.candidate_hash or ""
    service.approve(ANA, p.proposal_id, h)
    return service.publish(ANA, p.proposal_id, key).release_id


def test_immutable_tables_reject_update_and_delete(registry_store: PgRegistryStore,
                                                   admin_conn: "psycopg.Connection[Any]") -> None:  # T-REG-01
    _service(registry_store).import_seed(ANA, REGISTRY_DEMO)
    with registry_store.connect() as conn:  # rol de aplicación: sin permiso
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE reg_entity_versions SET created_by = 'x'")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM reg_releases")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # administrador: lo frena el trigger (42501)
        admin_conn.execute("UPDATE reg_entity_versions SET created_by = 'x'")


def test_publish_failure_mid_way_leaves_nothing(registry_store: PgRegistryStore) -> None:  # T-REG-02
    service = _service(registry_store)
    service.import_seed(ANA, REGISTRY_DEMO)
    registry_store.fail_on = lambda name: name == "set_alias"
    with pytest.raises(RuntimeError):
        _publish(service)
    registry_store.fail_on = None
    with registry_store.transaction() as tx:
        assert [s.ref.version for s in tx.list_versions("prompt", "p/resumen_radicado")] == ["1.0.0"]


def test_second_publish_on_same_agent_is_stale(registry_store: PgRegistryStore) -> None:  # T-REG-16
    service = _service(registry_store)
    service.import_seed(ANA, REGISTRY_DEMO)
    late = service.create_proposal(ANA, AGENT, Origin.manual, "tardía")  # base: la release importada
    service.put_draft(ANA, late.proposal_id, [prompt_draft(version="1.2.0", text="Otra."), SUITE],
                      expected_rev=0)
    service.freeze(ANA, late.proposal_id)
    service.evaluate(ANA, late.proposal_id, "disputas-suite")
    h = service.get_proposal(late.proposal_id).proposal.candidate_hash or ""
    service.approve(ANA, late.proposal_id, h)
    _publish(service, "ka", "1.1.0")  # otra propuesta mueve staging primero
    with pytest.raises(RegistryError) as info:
        service.publish(ANA, late.proposal_id, "kb")
    assert info.value.code is RegistryErrorCode.proposal_stale


@pytest.fixture
def pg_registry(registry_store: PgRegistryStore) -> PostgresRegistry:
    """Contrato de M0: carga las entidades del contrato como publicadas (rel-1 en prod, rel-2 en 2.0.0)."""
    from agent_core.registry.entities import content_hash, encode_entity, version_ref
    from agent_core.registry.models import AliasChange, StoredRelease, StoredVersion, VersionDocs
    from testing.builders import NOW

    entities = [Flow.model_validate(EXACT_FLOW), Flow.model_validate(RANGED_FLOW),
                Template(id="t/saludo", version="1.0.0", locales={"es": "Hola"}), KNOWLEDGE]
    docs = VersionDocs(description="contrato", rationale="", changelog="")
    with registry_store.transaction() as tx:
        for e in entities:
            tx.blobs.put(encode_entity(e))
            tx.insert_version(StoredVersion(ref=version_ref(e), content_hash=content_hash(e), docs=docs,
                                            proposal_id=None, created_by="t", created_at=NOW))
        for rid, version in (("rel-1", "1.0.0"), ("rel-2", "2.0.0")):
            release = Release.model_validate(
                {"id": rid, "status": "active", "language_detection": "lang@1.0.0"})
            tx.insert_release(StoredRelease(release=release, release_hash="h" * 64, agent_id="atencion",
                                            agent_version=version, base_release_id=None, proposal_id=None,
                                            published_by="t", published_at=NOW), [])
        tx.set_alias(AliasChange(agent_id="atencion", alias="prod", before=None, after="rel-1", actor="t",
                                 reason="r", at=NOW))
    return PostgresRegistry(registry_store, FakeClock())


def test_postgres_registry_passes_contract(pg_registry: PostgresRegistry) -> None:  # T-REG-17
    check_get_exact_entity(pg_registry)
    check_get_with_non_exact_content_raises(pg_registry)
    check_resolve_release_by_alias_and_version(pg_registry)
    check_get_knowledge_snapshot(pg_registry)
    with pytest.raises(KeyError):
        pg_registry.get(EntityRef.parse("flujo@9.9.9"), Flow)


def test_candidates_and_drafts_are_invisible(registry_store: PgRegistryStore) -> None:  # T-REG-18
    service = _service(registry_store)
    service.import_seed(ANA, REGISTRY_DEMO)
    p = service.create_proposal(ANA, AGENT, Origin.manual, "t")
    service.put_draft(ANA, p.proposal_id, [prompt_draft()], expected_rev=0)
    service.freeze(ANA, p.proposal_id)
    reg = PostgresRegistry(registry_store, FakeClock())
    with pytest.raises(KeyError):
        from agent_core.domain import Prompt
        reg.get(EntityRef.parse("p/resumen_radicado@1.1.0"), Prompt)
    assert reg.resolve_release(AgentSelector.parse(f"{AGENT}@staging"), principal()).entities


def test_tampered_blob_raises_integrity_error(registry_store: PgRegistryStore,
                                              admin_conn: "psycopg.Connection[Any]") -> None:  # T-REG-19
    _service(registry_store).import_seed(ANA, REGISTRY_DEMO)
    admin_conn.execute("ALTER TABLE reg_blobs DISABLE TRIGGER USER")
    admin_conn.execute("UPDATE reg_blobs SET bytes = 'x'::bytea")
    admin_conn.execute("ALTER TABLE reg_blobs ENABLE TRIGGER USER")
    reg = PostgresRegistry(registry_store, FakeClock())
    with pytest.raises(IntegrityError):
        from agent_core.domain import Prompt
        reg.get(EntityRef.parse("p/resumen_radicado@1.0.0"), Prompt)


def test_revoked_release_not_resolved_for_new_runs(registry_store: PgRegistryStore) -> None:  # T-REG-20, RF 5
    service = _service(registry_store)
    service.import_seed(ANA, REGISTRY_DEMO)
    rel = _publish(service)
    clock = FakeClock()
    reg = PostgresRegistry(registry_store, clock, status_ttl=timedelta(seconds=5))
    assert reg.resolve_release(AgentSelector.parse(f"{AGENT}@staging"), principal()).id == rel
    service.revoke(ANA, rel, "falla en producción")
    clock.advance(timedelta(seconds=6))
    assert reg.release_status(rel) == "revoked"
    with pytest.raises(KeyError):
        reg.resolve_release(AgentSelector.parse(f"{AGENT}@staging"), principal())
