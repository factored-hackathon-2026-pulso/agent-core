"""`TranscriptStore` sobre Postgres real: el contrato del puerto más lo que solo la persistencia ofrece."""

from collections.abc import Iterator

import pytest

from agent_core.adapters.postgres_transcript import PgTranscriptStore
from agent_core.adapters.postgres_uow import PostgresStore
from tests.contracts.test_transcript_contract import (  # noqa: F401  (se corren contra este backend)
    entry,
    test_append_returns_distinct_ids_and_read_keeps_order,
    test_read_is_scoped_by_run,
    test_recent_turns_returns_last_n_entries_oldest_first,
    test_write_turn_does_not_touch_other_runs_or_turns,
    test_write_turn_is_idempotent_and_the_last_attempt_wins,
    test_write_turn_refuses_entries_of_another_run_or_turn_and_writes_nothing,
    test_write_turn_stores_the_entries_in_order_and_returns_their_ids,
)
from tests.support.pg import ADMIN_DSN, postgres_store

pytestmark = pytest.mark.integration

SCHEMA = "transcript_pg"


@pytest.fixture
def store() -> Iterator[PgTranscriptStore]:  # sobrescribe el `store` parametrizado del contrato
    with postgres_store(SCHEMA) as pg:
        yield PgTranscriptStore(pg)


def test_entries_survive_a_new_store_instance() -> None:
    with postgres_store(SCHEMA) as pg:
        PgTranscriptStore(pg).append(entry(text="persiste ⟦name:1⟧"))

        again = PgTranscriptStore(PostgresStore(ADMIN_DSN, schema=SCHEMA))

        assert [e.text_model for e in again.read("run-0001")] == ["persiste ⟦name:1⟧"]


def test_a_rejected_draft_keeps_its_reason() -> None:
    with postgres_store(SCHEMA) as pg:
        store = PgTranscriptStore(pg)
        store.append(entry(role="rejected_draft", text="borrador").model_copy(update={"reason": "pii_leak"}))

        [read] = store.read("run-0001")

        assert (read.role, read.reason) == ("rejected_draft", "pii_leak")


def test_delete_run_removes_only_that_run() -> None:
    with postgres_store(SCHEMA) as pg:
        store = PgTranscriptStore(pg)
        store.append(entry(run_id="run-0001"))
        store.append(entry(run_id="run-0002"))

        store.delete_run("run-0001")

        assert store.read("run-0001") == [] and len(store.read("run-0002")) == 1


def test_recent_turns_with_more_than_available_returns_all_in_order() -> None:
    with postgres_store(SCHEMA) as pg:
        store = PgTranscriptStore(pg)
        for i in range(3):
            store.append(entry(text=f"t{i}"))

        assert [e.text_model for e in store.recent_turns("run-0001", 10)] == ["t0", "t1", "t2"]
