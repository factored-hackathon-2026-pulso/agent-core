from datetime import timedelta

import pytest

from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import AuditContext, DraftWrite, RegistryEvent
from testing.builders import NOW


def _write(key: str = "k1", **over: object) -> DraftWrite:
    base: dict[str, object] = {
        "idempotency_key": key, "op": "put_draft", "proposal_id": "prop-1", "rev_after": 1,
        "request_hash": "a" * 64, "created_at": NOW}
    return DraftWrite.model_validate(base | over)


def test_put_and_get_draft_write_round_trip() -> None:
    store = InMemoryRegistryStore()
    write = _write(audit=AuditContext(run_id="run-1", on_behalf_of="builder:ana"))
    with store.transaction() as tx:
        assert tx.get_draft_write("k1") is None
        tx.put_draft_write(write)
    with store.transaction() as tx:
        assert tx.get_draft_write("k1") == write


def test_a_repeated_key_is_rejected_and_rolled_back() -> None:
    store = InMemoryRegistryStore()
    with store.transaction() as tx:
        tx.put_draft_write(_write())
    with pytest.raises(ValueError):
        with store.transaction() as tx:
            tx.put_draft_write(_write(rev_after=9))
    with store.transaction() as tx:
        stored = tx.get_draft_write("k1")
        assert stored is not None and stored.rev_after == 1


def _created(origin: str, at: object) -> RegistryEvent:
    return RegistryEvent.model_validate({"type": "proposal_created", "actor": "bot",
                                         "principal_type": "builder", "origin": origin,
                                         "proposal_id": "p", "at": at})


def test_count_created_after_is_strict_and_filters_by_origin() -> None:
    store = InMemoryRegistryStore()
    with store.transaction() as tx:
        tx.append_event(_created("auto_detect", NOW - timedelta(hours=24)))  # justo en el borde: no cuenta
        tx.append_event(_created("auto_detect", NOW - timedelta(hours=23)))
        tx.append_event(_created("manual", NOW - timedelta(hours=1)))
    with store.transaction() as tx:
        assert tx.count_created_after("auto_detect", NOW - timedelta(hours=24)) == 1
        assert tx.count_created_after("manual", NOW - timedelta(hours=24)) == 1
        assert tx.count_created_after("import", NOW - timedelta(hours=24)) == 0


def test_count_eval_runs_of_an_unknown_proposal_is_zero() -> None:
    store = InMemoryRegistryStore()
    with store.transaction() as tx:
        assert tx.count_eval_runs("nope") == 0
        assert tx.get_eval_run("nope") is None
