"""Contrato de `UnitOfWork`, `AuditSink`, `Outbox` y `CostCounters`: cada `check_*` corre contra cualquier
backend (hoy el doble en memoria; el adaptador Postgres de M3/M4/M11 se agrega como parámetro de `backend`).
Las fallas inyectables y las pruebas de concurrencia propias del doble van al final, fuera del contrato."""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import TypeAdapter

from agent_core.domain import (
    AnyEvent,
    OutboxMessage,
    PrincipalKey,
    RunResult,
    TurnInProgress,
    VersionConflict,
)
from agent_core.ports import AuditSink, CostCounters, Outbox, UnitOfWorkFactory
from testing.builders import NOW, run_state
from testing.fakes.storage import (
    InMemoryAuditSink,
    InMemoryCostCounters,
    InMemoryOutbox,
    InMemoryStore,
    SimulatedCrash,
)
from tests.m00.samples import make_event

_EVENT_ADAPTER: TypeAdapter[Any] = TypeAdapter(AnyEvent)
HASH_A, HASH_B = "a" * 64, "b" * 64
_CLOSED = {"outcome": "resolved", "closed_by": "flow"}
EVENT = _EVENT_ADAPTER.validate_python(
    {**make_event("run_closed"), "payload": _CLOSED, "seq": 0, "prev_hash": None, "hash": HASH_A}
)
EVENT_2 = _EVENT_ADAPTER.validate_python(
    {**make_event("run_closed"), "event_id": "event-0002", "payload": _CLOSED,
     "seq": 1, "prev_hash": HASH_A, "hash": HASH_B}
)
MSG = OutboxMessage(message_id="message-0001", type="handoff_created", run_id="run-0001", payload={"k": 1},
                    created_at=NOW)
MSG_2 = MSG.model_copy(update={"message_id": "message-0002"})
TTL = timedelta(seconds=30)
CUSTOMER = PrincipalKey(type="customer", id="cust-001")
OTHER = PrincipalKey(type="customer", id="cust-002")


@dataclass
class Backend:
    factory: UnitOfWorkFactory
    audit: AuditSink
    outbox: Outbox
    costs: CostCounters


@pytest.fixture(params=["memory"])
def backend(request: pytest.FixtureRequest) -> Backend:
    store = InMemoryStore()
    return Backend(store.uow, InMemoryAuditSink(store), InMemoryOutbox(store), InMemoryCostCounters(store))


def _seed(factory: UnitOfWorkFactory, **over: Any) -> None:
    with factory() as uow:
        uow.save_run(run_state(**over), expected_version=0)
        uow.commit()


# --- UnitOfWork: transacción, versión y aislamiento -------------------------------------------------------


def check_nothing_persists_without_commit(b: Backend) -> None:
    with b.factory() as uow:
        uow.save_run(run_state(), expected_version=0)
        uow.append_events("run-0001", [EVENT])
        uow.enqueue_outbox(MSG)
        uow.add_usage(CUSTOMER, Decimal("1.5"), NOW)
        uow.put_handoff("handoff-0001", {"a": 1})
    with b.factory() as uow:
        assert uow.load_run("run-0001") is None
        assert uow.last_event("run-0001") is None
        assert uow.get_handoff("handoff-0001") is None
    assert b.audit.read("run-0001") == []
    assert b.outbox.pending(10) == []
    assert b.costs.spent_today(CUSTOMER, NOW) == Decimal(0)


def check_uow_reads_its_own_writes(b: Backend) -> None:
    with b.factory() as uow:
        saved = uow.save_run(run_state(), expected_version=0)
        assert uow.load_run("run-0001") == saved
        found = uow.find_run_by_session("session-0001")
        assert found is not None
        assert found.run_id == "run-0001"
        uow.append_events("run-0001", [EVENT, EVENT_2])
        assert uow.last_event("run-0001") == EVENT_2


def check_save_run_versions_and_conflicts(b: Backend) -> None:
    with b.factory() as uow:
        saved = uow.save_run(run_state(), expected_version=0)
        assert saved.state_version == 1
        uow.commit()
    with b.factory() as uow:
        loaded = uow.load_run("run-0001")
        assert loaded is not None
        with pytest.raises(VersionConflict):
            uow.save_run(loaded, expected_version=0)
        with pytest.raises(VersionConflict):
            uow.save_run(loaded, expected_version=5)
        assert uow.save_run(loaded, expected_version=1).state_version == 2


def check_stale_writer_cannot_overwrite_at_commit(b: Backend) -> None:
    """Dos transacciones cargan la misma versión: la segunda en commitear pierde (sin lost update)."""
    _seed(b.factory)
    with b.factory() as winner, b.factory() as loser:
        base = winner.load_run("run-0001")
        assert base is not None
        assert loser.load_run("run-0001") == base
        winner.save_run(base, expected_version=1)
        loser.save_run(base.model_copy(update={"locale": "en"}), expected_version=1)
        winner.commit()
        with pytest.raises(VersionConflict):
            loser.commit()
    with b.factory() as uow:
        final = uow.load_run("run-0001")
        assert final is not None
        assert (final.state_version, final.locale) == (2, "es")


def check_conflicting_commit_applies_nothing(b: Backend) -> None:
    _seed(b.factory)
    with b.factory() as winner, b.factory() as loser:
        base = winner.load_run("run-0001")
        assert base is not None
        winner.save_run(base, expected_version=1)
        loser.save_run(base, expected_version=1)
        loser.append_events("run-0001", [EVENT])
        loser.enqueue_outbox(MSG)
        loser.add_usage(CUSTOMER, Decimal("1"), NOW)
        winner.commit()
        with pytest.raises(VersionConflict):
            loser.commit()
    assert b.audit.read("run-0001") == []
    assert b.outbox.pending(10) == []
    assert b.costs.spent_today(CUSTOMER, NOW) == Decimal(0)


def check_persisted_state_is_isolated_from_callers(b: Backend) -> None:
    with b.factory() as uow:
        state = run_state()
        saved = uow.save_run(state, expected_version=0)
        state.pending_intents.append(
            {"flow": "x", "priority": 1, "mention_order": 1})  # type: ignore[arg-type]
        saved.locale = "en"
        uow.commit()
    with b.factory() as uow:
        loaded = uow.load_run("run-0001")
        assert loaded is not None
        assert loaded.pending_intents == [] and loaded.locale == "es"
        loaded.locale = "en"
        again = uow.load_run("run-0001")
        assert again is not None
        assert again.locale == "es"


def check_find_run_by_session(b: Backend) -> None:
    _seed(b.factory)
    with b.factory() as uow:
        found = uow.find_run_by_session("session-0001")
        assert found is not None
        assert found.run_id == "run-0001"
        assert uow.find_run_by_session("session-nope") is None


def check_save_run_revalidates_state(b: Backend) -> None:
    broken = run_state()
    broken.outcome = "resolved"  # type: ignore[assignment]  # la asignación no valida: la UoW sí
    with b.factory() as uow, pytest.raises(ValueError, match="outcome"):
        uow.save_run(broken, expected_version=0)


def check_finished_uow_rejects_writes(b: Backend) -> None:
    with b.factory() as uow:
        uow.commit()
        with pytest.raises(RuntimeError):
            uow.save_run(run_state(), expected_version=0)
        with pytest.raises(RuntimeError):
            uow.commit()


# --- lease de turno ---------------------------------------------------------------------------------------


def check_lease_is_immediate_and_expires(b: Backend) -> None:
    with b.factory() as first:
        first.acquire_turn("run-0001", "turn-a", NOW, TTL)  # sin commit: visible de inmediato
        with b.factory() as other:
            with pytest.raises(TurnInProgress):
                other.acquire_turn("run-0001", "turn-b", NOW + timedelta(seconds=5), TTL)
            with pytest.raises(TurnInProgress):  # un instante antes del vencimiento
                other.acquire_turn("run-0001", "turn-b", NOW + TTL - timedelta(microseconds=1), TTL)
            other.acquire_turn("run-0001", "turn-b", NOW + TTL + timedelta(seconds=1), TTL)


def check_lease_is_per_run_and_reentrant_for_same_turn(b: Backend) -> None:
    with b.factory() as uow:
        uow.acquire_turn("run-0001", "turn-a", NOW, TTL)
        uow.acquire_turn("run-0001", "turn-a", NOW + timedelta(seconds=1), TTL)  # mismo turno: renueva
        uow.acquire_turn("run-0002", "turn-b", NOW, TTL)  # otro run: independiente
    with b.factory() as other, pytest.raises(TurnInProgress):
        other.acquire_turn("run-0001", "turn-b", NOW + timedelta(seconds=30), TTL)  # renovado hasta +31s


def check_lease_rejects_non_positive_ttl(b: Backend) -> None:
    with b.factory() as uow, pytest.raises(ValueError):
        uow.acquire_turn("run-0001", "turn-a", NOW, timedelta(0))


def check_release_turn_applies_on_commit(b: Backend) -> None:
    with b.factory() as uow:
        uow.acquire_turn("run-0001", "turn-a", NOW, TTL)
        uow.release_turn("run-0001", "turn-a")
        with b.factory() as other, pytest.raises(TurnInProgress):  # sin commit sigue tomado
            other.acquire_turn("run-0001", "turn-b", NOW + timedelta(seconds=1), TTL)
        uow.commit()
    with b.factory() as other:
        other.acquire_turn("run-0001", "turn-b", NOW + timedelta(seconds=1), TTL)


def check_release_only_frees_own_lease(b: Backend) -> None:
    with b.factory() as uow:
        uow.acquire_turn("run-0001", "turn-b", NOW, TTL)
    with b.factory() as stale:
        stale.release_turn("run-0001", "turn-a")  # turno ajeno o vencido: no libera el lease de turn-b
        stale.commit()
    with b.factory() as other, pytest.raises(TurnInProgress):
        other.acquire_turn("run-0001", "turn-c", NOW + timedelta(seconds=1), TTL)


# --- resultados de turno, idempotencia, handoffs ---------------------------------------------------------


def check_turn_results_idempotency_and_handoffs(b: Backend) -> None:
    result = RunResult(run_id="run-0001", release="rel-1", status="open", trace_id="trace-1")
    with b.factory() as uow:
        uow.put_run_idempotency(CUSTOMER, "idem-1", "hash-body", result)
        uow.put_handoff("handoff-0001", {"reason_code": "tool_failure"})
        uow.commit()
    with b.factory() as uow:
        assert uow.get_run_idempotency(CUSTOMER, "idem-1") == ("hash-body", result)
        assert uow.get_handoff("handoff-0001") == {"reason_code": "tool_failure"}
        assert uow.get_turn_result("run-0001", "c-1") is None


def check_idempotency_is_scoped_by_principal(b: Backend) -> None:
    result = RunResult(run_id="run-0001", release="rel-1", status="open", trace_id="trace-1")
    with b.factory() as uow:
        uow.put_run_idempotency(CUSTOMER, "idem-1", "hash-body", result)
        assert uow.get_run_idempotency(OTHER, "idem-1") is None  # ni dentro de la misma transacción
        uow.commit()
    with b.factory() as uow:
        assert uow.get_run_idempotency(OTHER, "idem-1") is None
        assert uow.get_run_idempotency(PrincipalKey(type="advisor", id="cust-001"), "idem-1") is None
        assert uow.get_run_idempotency(CUSTOMER, "idem-2") is None


def check_first_idempotency_record_wins(b: Backend) -> None:
    first = RunResult(run_id="run-0001", release="rel-1", status="open", trace_id="trace-1")
    second = RunResult(run_id="run-0002", release="rel-1", status="open", trace_id="trace-2")
    with b.factory() as uow:
        uow.put_run_idempotency(CUSTOMER, "idem-1", "hash-1", first)
        uow.commit()
    with b.factory() as uow:
        uow.put_run_idempotency(CUSTOMER, "idem-1", "hash-2", second)
        uow.commit()
    with b.factory() as uow:
        assert uow.get_run_idempotency(CUSTOMER, "idem-1") == ("hash-1", first)


def check_empty_handoff_packet_is_returned(b: Backend) -> None:
    with b.factory() as uow:
        uow.put_handoff("handoff-0001", {})
        uow.commit()
    with b.factory() as uow:
        assert uow.get_handoff("handoff-0001") == {}


def check_handoff_is_isolated_from_callers(b: Backend) -> None:
    packet: dict[str, Any] = {"nested": {"k": [1]}}
    with b.factory() as uow:
        uow.put_handoff("handoff-0001", packet)
        packet["nested"]["k"].append(2)
        uow.commit()
    with b.factory() as uow:
        got = uow.get_handoff("handoff-0001")
        assert got == {"nested": {"k": [1]}}
        assert got is not None
        got["nested"] = "mutado"
        assert uow.get_handoff("handoff-0001") == {"nested": {"k": [1]}}


# --- inactividad ------------------------------------------------------------------------------------------


def check_list_inactive_uses_inactive_after(b: Backend) -> None:
    with b.factory() as uow:
        uow.save_run(run_state(run_id="run-a", session_id="s-a", inactive_after=NOW + timedelta(minutes=5)),
                     0)
        uow.save_run(run_state(run_id="run-b", session_id="s-b", inactive_after=NOW + timedelta(minutes=1)),
                     0)
        uow.save_run(run_state(run_id="run-c", session_id="s-c", inactive_after=NOW + timedelta(hours=1)), 0)
        uow.save_run(run_state(run_id="run-d", session_id="s-d", inactive_after=None), 0)
        uow.commit()
    with b.factory() as uow:
        assert uow.list_inactive(NOW + timedelta(minutes=10), limit=10) == ["run-b", "run-a"]
        assert uow.list_inactive(NOW + timedelta(minutes=10), limit=1) == ["run-b"]
        assert uow.list_inactive(NOW + timedelta(minutes=1), limit=10) == []  # estrictamente menor


def check_list_inactive_follows_state_changes(b: Backend) -> None:
    _seed(b.factory, inactive_after=NOW + timedelta(minutes=1))
    later = NOW + timedelta(minutes=10)
    with b.factory() as uow:
        run = uow.load_run("run-0001")
        assert run is not None
        assert uow.list_inactive(later, 10) == ["run-0001"]
        run.inactive_after = NOW + timedelta(hours=1)  # actividad nueva, sin commitear aún
        uow.save_run(run, expected_version=1)
        assert uow.list_inactive(later, 10) == []  # la UoW ve su propia escritura
    with b.factory() as uow:
        assert uow.list_inactive(later, 10) == ["run-0001"]  # lo no commiteado se descarta
    with b.factory() as uow:
        run = uow.load_run("run-0001")
        assert run is not None
        run.inactive_after = NOW + timedelta(hours=1)
        uow.save_run(run, expected_version=1)
        uow.commit()
    with b.factory() as uow:
        assert uow.list_inactive(later, 10) == []


def check_list_inactive_skips_closed_runs(b: Backend) -> None:
    _seed(b.factory, inactive_after=NOW + timedelta(minutes=1))
    with b.factory() as uow:
        run = uow.load_run("run-0001")
        assert run is not None
        closed = run.model_copy(update={"status": "closed", "outcome": "resolved", "closed_at": NOW,
                                                "inactive_after": None})
        uow.save_run(closed, expected_version=1)
        uow.commit()
    with b.factory() as uow:
        assert uow.list_inactive(NOW + timedelta(hours=1), 10) == []


# --- auditoría y outbox -----------------------------------------------------------------------------------


def check_audit_reads_committed_in_order_preserving_chain(b: Backend) -> None:
    with b.factory() as uow:
        uow.append_events("run-0001", [EVENT, EVENT_2])
        uow.commit()
    assert b.audit.read("run-0001") == [EVENT, EVENT_2]
    assert [(e.seq, e.prev_hash, e.hash) for e in b.audit.read("run-0001")] == [
        (0, None, HASH_A), (1, HASH_A, HASH_B)]
    b.audit.append_outside_turn("run-0001", [EVENT_2.model_copy(update={"event_id": "event-0003"})])
    assert [e.event_id for e in b.audit.read("run-0001")] == ["event-0001", "event-0002", "event-0003"]
    assert b.audit.read("run-9999") == []


def check_audit_is_append_only_and_readers_get_copies(b: Backend) -> None:
    with b.factory() as uow:
        uow.append_events("run-0001", [EVENT])
        uow.commit()
    b.audit.read("run-0001").clear()
    assert b.audit.read("run-0001") == [EVENT]
    public = {n for n in dir(b.audit) if not n.startswith("_")}
    assert public == {"read", "append_outside_turn"}  # sin update/delete/clear


def check_outbox_delivery_is_ordered_and_idempotent(b: Backend) -> None:
    with b.factory() as uow:
        uow.enqueue_outbox(MSG)
        uow.enqueue_outbox(MSG_2)
        uow.commit()
    assert [m.message_id for m in b.outbox.pending(10)] == ["message-0001", "message-0002"]
    assert [m.message_id for m in b.outbox.pending(1)] == ["message-0001"]
    assert b.outbox.pending(0) == []
    b.outbox.mark_delivered("message-0001")
    b.outbox.mark_delivered("message-0001")  # idempotente
    assert [m.message_id for m in b.outbox.pending(10)] == ["message-0002"]


def check_outbox_is_at_least_once(b: Backend) -> None:
    with b.factory() as uow:
        uow.enqueue_outbox(MSG)
        uow.commit()
    assert b.outbox.pending(10) == b.outbox.pending(10) == [MSG]  # leer no consume: sin ack se reentrega
    b.outbox.mark_delivered("message-9999")  # id desconocido: no-op, no adelanta entregas futuras
    with b.factory() as uow:
        uow.enqueue_outbox(MSG.model_copy(update={"message_id": "message-9999"}))
        uow.commit()
    assert [m.message_id for m in b.outbox.pending(10)] == ["message-0001", "message-9999"]


def check_outbox_ignores_duplicate_message_ids(b: Backend) -> None:
    with b.factory() as uow:
        uow.enqueue_outbox(MSG)
        uow.enqueue_outbox(MSG)
        uow.commit()
    assert len(b.outbox.pending(10)) == 1
    b.outbox.mark_delivered("message-0001")
    assert b.outbox.pending(10) == []
    with b.factory() as uow:
        uow.enqueue_outbox(MSG)  # reintento tras entrega: no resucita
        uow.commit()
    assert b.outbox.pending(10) == []


# --- costos -----------------------------------------------------------------------------------------------


def check_usage_is_summed_per_principal_on_commit(b: Backend) -> None:
    with b.factory() as uow:
        uow.add_usage(CUSTOMER, Decimal("0.10"), NOW)
        uow.add_usage(CUSTOMER, Decimal("0.20"), NOW + timedelta(minutes=5))
        uow.add_usage(OTHER, Decimal("9"), NOW)
        uow.commit()
    assert b.costs.spent_today(CUSTOMER, NOW + timedelta(minutes=10)) == Decimal("0.30")
    assert b.costs.spent_today(OTHER, NOW) == Decimal("9")
    assert b.costs.hits(CUSTOMER, timedelta(hours=1), NOW + timedelta(minutes=10)) == 2
    assert b.costs.hits(CUSTOMER, timedelta(minutes=6), NOW + timedelta(minutes=10)) == 1  # ventana móvil
    assert b.costs.hits(PrincipalKey(type="customer", id="nadie"), timedelta(hours=1), NOW) == 0


def check_spent_today_is_per_utc_day(b: Backend) -> None:
    with b.factory() as uow:
        uow.add_usage(CUSTOMER, Decimal("1"), NOW)
        uow.add_usage(CUSTOMER, Decimal("2"), NOW + timedelta(days=1))
        uow.commit()
    assert b.costs.spent_today(CUSTOMER, NOW) == Decimal("1")
    assert b.costs.spent_today(CUSTOMER, NOW + timedelta(days=1)) == Decimal("2")


def check_usage_rejects_negative_cost(b: Backend) -> None:
    with b.factory() as uow, pytest.raises(ValueError):
        uow.add_usage(CUSTOMER, Decimal("-0.01"), NOW)


CHECKS: list[Callable[[Backend], None]] = [
    obj for name, obj in sorted(globals().items()) if name.startswith("check_") and callable(obj)
]


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__.removeprefix("check_"))
def test_contract(backend: Backend, check: Callable[[Backend], None]) -> None:
    check(backend)


# --- propias del doble en memoria (no forman parte del contrato) -----------------------------------------


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


def test_on_commit_fault_applies_nothing(store: InMemoryStore) -> None:
    store.inject("on_commit")
    with pytest.raises(SimulatedCrash), store.uow() as uow:
        uow.save_run(run_state(), expected_version=0)
        uow.append_events("run-0001", [EVENT])
        uow.enqueue_outbox(MSG)
        uow.add_usage(CUSTOMER, Decimal("1"), NOW)
        uow.commit()
    with store.uow() as uow:
        assert uow.load_run("run-0001") is None
    assert InMemoryAuditSink(store).read("run-0001") == []
    assert InMemoryOutbox(store).pending(10) == []
    assert InMemoryCostCounters(store).spent_today(CUSTOMER, NOW) == Decimal(0)


def test_after_commit_fault_keeps_changes(store: InMemoryStore) -> None:
    store.inject("after_commit")
    with pytest.raises(SimulatedCrash), store.uow() as uow:
        uow.save_run(run_state(), expected_version=0)
        uow.commit()
    with store.uow() as uow:
        assert uow.load_run("run-0001") is not None


def test_faults_fire_in_order_and_only_at_their_point(store: InMemoryStore) -> None:
    store.inject("after_commit")
    with store.uow() as uow:  # el commit no cruza on_commit: solo dispara la falla after_commit
        uow.save_run(run_state(), expected_version=0)
        with pytest.raises(SimulatedCrash):
            uow.commit()
    with store.uow() as uow:
        uow.commit()  # ya consumida


def test_concurrent_writers_with_same_base_version_have_one_winner(store: InMemoryStore) -> None:
    _seed(store.uow)
    outcomes: list[str] = []
    barrier = threading.Barrier(8)

    def writer() -> None:
        with store.uow() as uow:
            run = uow.load_run("run-0001")
            assert run is not None
            uow.save_run(run, expected_version=1)
            barrier.wait()
            try:
                uow.commit()
                outcomes.append("ok")
            except VersionConflict:
                outcomes.append("conflict")

    threads = [threading.Thread(target=writer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["conflict"] * 7 + ["ok"]
