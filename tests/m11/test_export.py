"""Fuente de datos de la unidad 6 (§8) y T-M11-09 (mitad eventos): todo evento lleva run_id y release."""

from decimal import Decimal

from agent_core.audit.export import chain_integrity, export_events
from agent_core.audit.log import AuditLog
from agent_core.domain import EVENT_TYPES
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from tests.m11.helpers import EVENT_TYPES as SAMPLED
from tests.m11.helpers import event, events_of


def fill(store: InMemoryStore) -> AuditLog:
    log = AuditLog(InMemoryAuditSink(store), store.uow)
    with store.uow() as uow:
        log.append(uow, "run-0001", events_of("run-0001", 7))  # índice 6 = tool_called
        log.append(uow, "run-0002", [event("run_closed", run_id="run-0002", release="rel-otra")])
        uow.commit()
    return log


def test_t_m11_09_every_chained_event_carries_run_id_and_release() -> None:
    store = InMemoryStore()
    fill(store)
    for rows in (store.events["run-0001"], store.events["run-0002"]):
        for e in rows:
            assert e.run_id and e.release and e.seq is not None and e.hash


def test_sample_payloads_cover_every_event_type() -> None:
    assert set(SAMPLED) == set(EVENT_TYPES)  # si M0 agrega un evento, agrega su muestra


def test_export_rows_keep_measured_fields_and_filter_by_release() -> None:
    store = InMemoryStore()
    fill(store)
    sink = InMemoryAuditSink(store)
    rows = list(export_events(sink, ["run-0001", "run-0002"], release="rel-2026-09-28"))
    assert {r["run_id"] for r in rows} == {"run-0001"}
    tool = next(r for r in rows if r["type"] == "tool_called")
    assert "latency_ms" in tool["payload"]  # type: ignore[operator]
    assert all("hash" in r and "seq" in r for r in rows)


def test_chain_integrity_counts_intact_and_broken_runs() -> None:
    store = InMemoryStore()
    fill(store)
    store.events["run-0001"][2] = store.events["run-0001"][2].model_copy(update={"release": "x"})
    result = chain_integrity(InMemoryAuditSink(store), ["run-0001", "run-0002"])
    assert (result.runs, result.intact, result.broken) == (2, 1, ["run-0001"])
    assert result.ratio == Decimal("0.5")


def test_chain_integrity_of_no_runs_is_full() -> None:
    assert chain_integrity(InMemoryAuditSink(InMemoryStore()), []).ratio == Decimal(1)
