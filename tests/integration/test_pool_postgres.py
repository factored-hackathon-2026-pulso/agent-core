"""El pool de conexiones del `PostgresStore` (opt-in): reutiliza conexiones y respeta la UoW y el schema."""

from datetime import UTC, datetime

import pytest

from agent_core.adapters.postgres_uow import PostgresStore
from tests.support.pg import ADMIN_DSN, postgres_store

pytestmark = pytest.mark.integration


def test_pooled_store_reuses_connections_and_keeps_the_search_path() -> None:
    with postgres_store("pool_reuse"):
        store = PostgresStore(ADMIN_DSN, schema="pool_reuse", pool_max=2)
        store.open_pool()
        try:
            for _ in range(10):
                with store.reading() as conn:
                    row = conn.execute("SELECT current_schema()").fetchone()
                assert row == ("pool_reuse",)
            stats = store._pool.get_stats()  # type: ignore[union-attr]
            assert stats["pool_size"] <= 2 and stats["pool_available"] == stats["pool_size"]
        finally:
            store.close()


def test_a_uow_returns_its_connection_to_the_pool_even_when_it_fails() -> None:
    with postgres_store("pool_uow"):
        store = PostgresStore(ADMIN_DSN, schema="pool_uow", pool_max=1)
        store.open_pool()
        try:
            for _ in range(3):  # con max_size=1, una conexión no devuelta bloquearía la segunda vuelta
                with pytest.raises(RuntimeError, match="boom"), store.uow() as uow:
                    uow.list_inactive(datetime(2026, 10, 3, tzinfo=UTC), 1)
                    raise RuntimeError("boom")
            assert store.ping()
        finally:
            store.close()


def test_without_a_pool_nothing_changes() -> None:
    with postgres_store("pool_off") as store:
        with store.reading() as conn:
            assert conn.execute("SELECT 1").fetchone() == (1,)
        store.open_pool()  # no-op
        store.close()  # no-op
