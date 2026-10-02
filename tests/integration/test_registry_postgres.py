"""Registry sobre Postgres real (spec §13). Requiere docker compose up -d postgres."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from agent_core.domain import AgentSelector, EntityRef, Flow, Release, Template
from agent_core.ports import RegistryPort
from agent_core.registry.errors import IntegrityError, RegistryError, RegistryErrorCode
from agent_core.registry.models import Origin
from agent_core.registry.postgres.runtime import PostgresRegistry
from agent_core.registry.postgres.store import PgRegistryStore
from agent_core.registry.service import RegistryService
from testing.builders import principal
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.contracts.test_registry_contract import CHECKS, EXACT_FLOW, KNOWLEDGE, RANGED_FLOW
from tests.registry.helpers import AGENT, REGISTRY_DEMO, admin, human, prompt_draft, suite_draft
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
    _service(registry_store).import_seed(admin(), REGISTRY_DEMO)
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
    service.import_seed(admin(), REGISTRY_DEMO)
    registry_store.fail_on = lambda name: name == "set_alias"
    with pytest.raises(RuntimeError):
        _publish(service)
    registry_store.fail_on = None
    with registry_store.transaction() as tx:
        assert [s.ref.version for s in tx.list_versions("prompt", "p/resumen_radicado")] == ["1.0.0"]


def test_second_publish_on_same_agent_is_stale(registry_store: PgRegistryStore) -> None:  # T-REG-16
    service = _service(registry_store)
    service.import_seed(admin(), REGISTRY_DEMO)
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


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_postgres_registry_passes_contract(pg_registry: PostgresRegistry,
                                           check: Callable[[RegistryPort], None]) -> None:  # T-REG-17
    check(pg_registry)


def test_candidates_and_drafts_are_invisible(registry_store: PgRegistryStore) -> None:  # T-REG-18
    service = _service(registry_store)
    service.import_seed(admin(), REGISTRY_DEMO)
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
    _service(registry_store).import_seed(admin(), REGISTRY_DEMO)
    admin_conn.execute("ALTER TABLE reg_blobs DISABLE TRIGGER USER")
    admin_conn.execute("UPDATE reg_blobs SET bytes = 'x'::bytea")
    admin_conn.execute("ALTER TABLE reg_blobs ENABLE TRIGGER USER")
    reg = PostgresRegistry(registry_store, FakeClock())
    with pytest.raises(IntegrityError):
        from agent_core.domain import Prompt
        reg.get(EntityRef.parse("p/resumen_radicado@1.0.0"), Prompt)


def test_revoked_release_not_resolved_for_new_runs(registry_store: PgRegistryStore) -> None:  # T-REG-20, RF 5
    service = _service(registry_store)
    service.import_seed(admin(), REGISTRY_DEMO)
    rel = _publish(service)
    clock = FakeClock()
    reg = PostgresRegistry(registry_store, clock, status_ttl=timedelta(seconds=5))
    assert reg.resolve_release(AgentSelector.parse(f"{AGENT}@staging"), principal()).id == rel
    service.revoke(admin(), rel, "falla en producción")
    clock.advance(timedelta(seconds=6))
    assert reg.release_status(rel) == "revoked"
    with pytest.raises(KeyError):
        reg.resolve_release(AgentSelector.parse(f"{AGENT}@staging"), principal())


def test_end_to_end_prompt_change(registry_store: PgRegistryStore) -> None:  # T-REG-27
    from agent_core.registry import EntityDraft, LocalSandbox, ScenarioEvaluator, VersionDocs
    from testing.registry_demo import build_harness, demo_suite

    clock, ids = FakeClock(), FakeIds()
    evaluator = ScenarioEvaluator(build_harness(), LocalSandbox(ids), max_workers=1)
    runs: dict[str, str] = {}

    class Runs:
        def release_of(self, run_id: str) -> str | None:
            return runs.get(run_id)

    service = RegistryService(registry_store, evaluator, clock, ids, runs=Runs())
    service.import_seed(admin(), REGISTRY_DEMO)

    suite = EntityDraft(kind="eval_suite", content=demo_suite().model_dump(mode="json"),
                        docs=VersionDocs(description="suite de disputas", rationale="gate",
                                         changelog="inicial"))
    p = service.create_proposal(ANA, AGENT, Origin.manual, "Confirmación más clara del radicado")
    service.put_draft(ANA, p.proposal_id, [prompt_draft(), suite], expected_rev=0)
    view = service.freeze(ANA, p.proposal_id)
    report = service.evaluate(ANA, p.proposal_id, "disputas-suite")
    assert report.verdict == "pass", report.model_dump()
    service.approve(ANA, p.proposal_id, view.candidate_hash)
    detail = service.publish(ANA, p.proposal_id, "e2e-1")

    reg = PostgresRegistry(registry_store, clock)
    release = reg.resolve_release(AgentSelector.parse(f"{AGENT}@staging"), principal())
    assert release.id == detail.release_id  # un run nuevo en staging usa la release nueva
    runs["run-e2e"] = release.id
    lineage = service.lineage_for_run(ANA, "run-e2e")
    changed = {e.ref.id: e.docs for e in lineage.entities if e.changed_vs_base}
    assert changed["p/resumen_radicado"].description == "cambio de prueba"
    unchanged = [e for e in lineage.entities if e.ref.id == "t/acuse"]  # no se tocó: igual que la base
    assert unchanged and not any(e.changed_vs_base for e in unchanged)
    assert {e.ref.id for e in lineage.entities if e.changed_vs_base} == set(changed)
    assert len(changed) < len(lineage.entities)
    assert lineage.approved_by == "ana" and lineage.eval_verdict == "pass"


def _first_publication_approved(service: RegistryService, text: str) -> str:
    """Propuesta sobre un agente sin alias: el borrador trae todas las entidades del agente (sin base)."""
    from agent_core.flows import kind_of
    from agent_core.registry import EntityDraft
    from tests.registry.helpers import demo_pinned, docs

    drafts = []
    for entity in demo_pinned().entities:
        if kind_of(entity) == "injection_ruleset":  # sin base, la release candidata no lo referencia
            continue
        content = entity.model_dump(mode="json", by_alias=True)
        if kind_of(entity) == "prompt":
            content["locales"] = {**content["locales"], "es": text}
        drafts.append(EntityDraft(kind=kind_of(entity), content=content, docs=docs()))
    p = service.create_proposal(ANA, AGENT, Origin.manual, text)
    assert p.base_release_id is None
    service.put_draft(ANA, p.proposal_id, [*drafts, SUITE], expected_rev=0)
    service.freeze(ANA, p.proposal_id)
    service.evaluate(ANA, p.proposal_id, "disputas-suite")
    h = service.get_proposal(p.proposal_id).proposal.candidate_hash or ""
    service.approve(ANA, p.proposal_id, h)
    return p.proposal_id


def test_concurrent_first_publications_without_alias_one_wins(
        registry_store: PgRegistryStore) -> None:
    """Revisión final I2: `FOR UPDATE` sobre un alias inexistente no bloquea; lo serializa el lock."""
    import threading
    import time

    service = _service(registry_store)
    pids = [_first_publication_approved(service, "Texto A."),
            _first_publication_approved(service, "Texto B.")]

    def slow_get_alias(name: str) -> bool:  # ensancha la ventana de la carrera entre leer y escribir el alias
        if name == "get_alias":
            time.sleep(0.4)
        return False

    registry_store.fail_on = slow_get_alias
    outcomes: dict[str, str] = {}

    def run(pid: str) -> None:
        try:
            outcomes[pid] = service.publish(ANA, pid, f"key-{pid}").release_id
        except RegistryError as exc:
            outcomes[pid] = exc.code.value
        except Exception as exc:  # la prueba reporta cualquier falla no tipada
            outcomes[pid] = f"{type(exc).__name__}"

    threads = [threading.Thread(target=run, args=(pid,)) for pid in pids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    registry_store.fail_on = None
    results = sorted(v if not v.startswith("rel-") else "published" for v in outcomes.values())
    assert results == ["proposal_stale", "published"], outcomes


def test_concurrent_seed_imports_of_same_agent_one_wins(registry_store: PgRegistryStore) -> None:
    import threading
    import time

    service = _service(registry_store)

    def slow_get_alias(name: str) -> bool:
        if name == "get_alias":
            time.sleep(0.4)
        return False

    registry_store.fail_on = slow_get_alias
    outcomes: list[str] = []

    def run() -> None:
        try:
            service.import_seed(admin(), REGISTRY_DEMO)
            outcomes.append("imported")
        except RegistryError as exc:
            outcomes.append(exc.code.value)
        except Exception as exc:  # la prueba reporta cualquier falla no tipada
            outcomes.append(type(exc).__name__)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    registry_store.fail_on = None
    assert sorted(outcomes) == ["illegal_transition", "imported"], outcomes


@pytest.mark.parametrize("table", ["reg_release_status", "reg_aliases", "reg_publish_keys", "reg_proposals",
                                   "reg_proposal_changes", "reg_releases", "reg_events"])
def test_app_role_cannot_delete_from_any_table(registry_store: PgRegistryStore, table: str) -> None:
    _service(registry_store).import_seed(admin(), REGISTRY_DEMO)
    with registry_store.connect() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"DELETE FROM {table}")


def test_app_role_privileges_on_mutable_tables_are_minimal(registry_store: PgRegistryStore) -> None:
    """Spec §3.2: las claves de idempotencia solo se insertan; el estado de release no se borra."""
    with registry_store.connect() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE reg_publish_keys SET release_id = 'x'")


def test_latest_release_for_agent_version_breaks_ties_by_release_id_desc(
        registry_store: PgRegistryStore) -> None:
    from agent_core.registry.models import StoredRelease
    from testing.builders import NOW

    with registry_store.transaction() as tx:
        for rid in ("rel-a", "rel-c", "rel-b"):
            release = Release.model_validate(
                {"id": rid, "status": "active", "language_detection": "lang@1.0.0"})
            tx.insert_release(StoredRelease(release=release, release_hash="h" * 64, agent_id="atencion",
                                            agent_version="1.0.0", base_release_id=None, proposal_id=None,
                                            published_by="t", published_at=NOW), [])
        assert tx.latest_release_for_agent_version("atencion", "1.0.0") == "rel-c"


def test_cli_cycle_on_postgres_with_export_to_disk(registry_store: PgRegistryStore, tmp_path: Any,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    from tests.composition.test_registry_cli import Cli

    cli = Cli(capsys, registry_store)
    [seed] = cli.ok("import", str(REGISTRY_DEMO), actor="admin")  # type: ignore[misc]
    assert cli.ok("show", cli.ok("propose", AGENT, "t")["proposal_id"])["proposal"]["state"] == "draft"  # type: ignore[index]
    assert cli.ok("export", seed["release_id"], str(tmp_path)) is None
    assert any(tmp_path.rglob("*.yaml"))


def test_release_records_its_eval_suite(registry_store: PgRegistryStore) -> None:  # ADR 0020 section 5
    evaluator = FakeEvaluator()
    service = RegistryService(registry_store, evaluator, FakeClock(), FakeIds())
    service.import_seed(admin(), REGISTRY_DEMO)
    rel = _publish(service)
    assert [str(r) for r in service.get_release(rel).eval_suite_refs] == ["eval_suite:disputas-suite@1.0.0"]
    _publish(service, "k2", "1.2.0")
    old = evaluator.requests[-1].old
    assert old is not None and old.suite is not None and old.suite.version == "1.0.0"


def test_release_eval_suites_are_insert_only(registry_store: PgRegistryStore) -> None:  # T-REG-01
    service = _service(registry_store)
    service.import_seed(admin(), REGISTRY_DEMO)
    _publish(service)
    with registry_store.connect() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM reg_release_eval_suites")


def test_approval_keeps_the_accepted_loosening(registry_store: PgRegistryStore) -> None:  # D7
    service = _service(registry_store)
    service.import_seed(admin(), REGISTRY_DEMO)
    _publish(service)
    p = service.create_proposal(ANA, AGENT, Origin.manual, "loosen")
    service.put_draft(ANA, p.proposal_id, [prompt_draft(version="1.2.0", text="Otra."),
                                            suite_draft("1.1.0", repetitions=1)], expected_rev=0)
    service.freeze(ANA, p.proposal_id)
    service.evaluate(ANA, p.proposal_id, "disputas-suite")
    h = service.get_proposal(p.proposal_id).proposal.candidate_hash or ""
    service.approve(ANA, p.proposal_id, h, accept_yardstick_loosened=True)
    with registry_store.transaction() as tx:
        approval = tx.latest_approval(p.proposal_id, h)
    assert approval is not None and [c.kind for c in approval.yardstick_loosened] == ["repetitions_lowered"]
def _seed_directory_agent(store: PgRegistryStore, agent_id: str, tag: str | None, *, release_id: str) -> None:
    from agent_core.domain import Agent
    from agent_core.registry.entities import content_hash, encode_entity, version_ref
    from agent_core.registry.models import AliasChange, StoredRelease, StoredVersion, VersionDocs
    from testing.builders import NOW
    from tests.m04.harness import agent_data

    card = None if tag is None else {"directory": tag, "summary": agent_id, "examples": []}
    agent = Agent.model_validate(agent_data(agent_id, routing=card, accepts={"slots": {}},
                                            understand="understand@1.0.0"))
    release = Release.model_validate({
        "id": release_id, "status": "active", "language_detection": "lang@1.0.0",
        "entities": {"agent": {agent_id: "1.0.0"}}})
    ref = version_ref(agent)
    with store.transaction() as tx:
        tx.blobs.put(encode_entity(agent))
        tx.insert_version(StoredVersion(ref=ref, content_hash=content_hash(agent),
                                        docs=VersionDocs(description="d", rationale="", changelog=""),
                                        proposal_id=None, created_by="t", created_at=NOW))
        tx.insert_release(StoredRelease(release=release, release_hash="h" * 64, agent_id=agent_id,
                                        agent_version="1.0.0", base_release_id=None, proposal_id=None,
                                        published_by="t", published_at=NOW), [ref])
        tx.set_alias(AliasChange(agent_id=agent_id, alias="prod", before=None, after=release_id, actor="t",
                                 reason="r", at=NOW))


def test_registry_directory_members_are_prod_agents_with_the_tag(registry_store: PgRegistryStore) -> None:
    from agent_core.registry import RegistryDirectory

    for agent_id, tag in (("disputas", "customer-care"), ("saldos", "customer-care"), ("interno", "staff"),
                          ("sin-ficha", None)):
        _seed_directory_agent(registry_store, agent_id, tag, release_id=f"rel-{agent_id}")
    runtime = PostgresRegistry(registry_store, FakeClock())
    directory = RegistryDirectory(registry_store, runtime, runtime.release)
    assert [(rid, a.id) for rid, a in directory.members("customer-care")] == [
        ("rel-disputas", "disputas"), ("rel-saldos", "saldos")]


def test_registry_directory_skips_revoked_releases(registry_store: PgRegistryStore) -> None:
    from agent_core.registry import RegistryDirectory

    _seed_directory_agent(registry_store, "disputas", "customer-care", release_id="rel-1")
    with registry_store.transaction() as tx:
        tx.set_release_status("rel-1", "revoked")
    runtime = PostgresRegistry(registry_store, FakeClock())
    assert RegistryDirectory(registry_store, runtime, runtime.release).members("customer-care") == []


def test_draft_writes_round_trip_and_are_insert_only(registry_store: PgRegistryStore,
                                                     admin_conn: "psycopg.Connection[Any]") -> None:
    from agent_core.registry.models import AuditContext, DraftWrite
    from testing.builders import NOW

    write = DraftWrite(idempotency_key="k1", op="put_draft", proposal_id="prop-1", rev_after=1,
                       request_hash="a" * 64, audit=AuditContext(run_id="run-1", on_behalf_of="builder:ana"),
                       created_at=NOW)
    with registry_store.transaction() as tx:
        tx.put_draft_write(write)
    with registry_store.transaction() as tx:
        assert tx.get_draft_write("k1") == write
        assert tx.get_draft_write("otra") is None
    with pytest.raises(psycopg.errors.UniqueViolation):
        with registry_store.transaction() as tx:
            tx.put_draft_write(write)
    with registry_store.connect() as conn:  # rol de aplicación: sin UPDATE ni DELETE
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE reg_draft_writes SET rev_after = 9")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM reg_draft_writes")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # administrador: lo frena el trigger
        admin_conn.execute("UPDATE reg_draft_writes SET rev_after = 9")


def test_counts_for_the_autonomous_builder_quotas(registry_store: PgRegistryStore) -> None:
    from datetime import timedelta

    from agent_core.registry.models import RegistryEvent
    from testing.builders import NOW

    def created(origin: str, at: object) -> RegistryEvent:
        return RegistryEvent.model_validate({"type": "proposal_created", "actor": "bot",
                                             "principal_type": "builder", "origin": origin,
                                             "proposal_id": "p", "at": at})

    with registry_store.transaction() as tx:
        tx.append_event(created("auto_detect", NOW - timedelta(hours=24)))  # en el borde: no cuenta
        tx.append_event(created("auto_detect", NOW - timedelta(hours=1)))
        tx.append_event(created("manual", NOW - timedelta(hours=1)))
    with registry_store.transaction() as tx:
        assert tx.count_created_after("auto_detect", NOW - timedelta(hours=24)) == 1
        assert tx.count_created_after("manual", NOW - timedelta(hours=24)) == 1
        assert tx.count_eval_runs("p") == 0
        assert tx.get_eval_run("nada") is None


def test_a_draft_is_never_served_as_executable_content(registry_store: PgRegistryStore) -> None:  # regla 8
    from agent_core.domain import Prompt

    service = _service(registry_store)
    service.import_seed(admin(), REGISTRY_DEMO)
    runtime = PostgresRegistry(registry_store, FakeClock())
    before = runtime.resolve_release(AgentSelector.parse(AGENT), principal())
    p = service.create_proposal(ANA, AGENT, Origin.manual, "borrador")
    service.put_draft(ANA, p.proposal_id,
                      [prompt_draft(version="9.9.9"), prompt_draft(version="1.0.0", id="p/solo_borrador")],
                      expected_rev=0)
    for ref in ("p/resumen_radicado@9.9.9", "p/solo_borrador@1.0.0"):  # versión nueva y entidad nueva
        with pytest.raises(KeyError):
            runtime.get(EntityRef.parse(ref), Prompt)
    after = runtime.resolve_release(AgentSelector.parse(AGENT), principal())
    assert after.id == before.id  # un borrador no mueve el alias ni crea una release
