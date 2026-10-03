"""Outbox sobre Postgres real: una fila de otra versión del motor no tumba la lectura."""

import psycopg
import pytest

from agent_core.relay import OutboxRelay
from testing.fakes.publisher import InMemoryPublisher
from tests.relay.test_relay import _message
from tests.support.pg import ADMIN_DSN, postgres_store

pytestmark = pytest.mark.integration

SCHEMA = "outbox_unknown"


def test_unknown_message_types_do_not_break_or_starve_the_relay() -> None:
    with postgres_store(SCHEMA) as store:
        with psycopg.connect(ADMIN_DSN, autocommit=True, options=f"-c search_path={SCHEMA}") as conn:
            conn.execute("INSERT INTO outbox (message_id, message_json) VALUES "
                         "('message-0000', %s), ('message-0001', %s)",
                         ('{"message_id": "message-0000", "type": "otro_tipo", "run_id": "r", '
                          '"payload": {}, "created_at": "2026-10-03T12:00:00Z"}', '{"basura": true}'))
            conn.execute("INSERT INTO outbox (message_id, message_json) VALUES ('message-0002', %s)",
                         (_message(2).model_dump_json(),))
        publisher = InMemoryPublisher()

        report = OutboxRelay(store.outbox(), publisher, batch=1).run_once()

        assert report.published == 1
        assert [p.event_id for p in publisher.sent] == ["message-0002"]
        assert store.outbox().pending(10) == []  # las filas ajenas quedan en la tabla, no en la cola
