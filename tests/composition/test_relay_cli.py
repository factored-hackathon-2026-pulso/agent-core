"""`agentcore relay`: configuración, una pasada, bucle con líder y parada limpia."""

import argparse
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from agent_core.composition.relay import add_relay_parser, run_relay
from agent_core.domain import OutboxMessage
from testing.fakes.publisher import InMemoryPublisher
from testing.fakes.storage import InMemoryOutbox, InMemoryStore

PAYLOAD = {"handoff_ref": "handoff-0001", "run_id": "run-0001", "target_queue": "disputas",
           "priority": "high", "reason_code": "low_confidence", "language": "es", "reportable_attrs": {}}


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_relay_parser(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(["relay", *argv])


def _outbox(n: int = 1) -> InMemoryOutbox:
    store = InMemoryStore()
    for i in range(1, n + 1):
        store.outbox_pending[f"m{i}"] = OutboxMessage(
            message_id=f"m{i}", type="handoff_created", run_id="run-0001", payload=PAYLOAD,  # type: ignore[arg-type]
            created_at=datetime(2026, 10, 3, tzinfo=UTC))
    return InMemoryOutbox(store)


def test_without_a_dsn_or_a_topic_it_exits_2_and_names_it(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_relay(_args("--once"), {}) == 2
    assert "AGENTCORE_REGISTRY_DSN" in capsys.readouterr().err
    assert run_relay(_args("--once", "--dsn", "postgresql://x/y"), {}) == 2
    assert "AGENTCORE_EVENTS_TOPIC_ARN" in capsys.readouterr().err


def test_once_publishes_and_reports(capsys: pytest.CaptureFixture[str]) -> None:
    bus = InMemoryPublisher()

    assert run_relay(_args("--once"), {}, publisher=bus, outbox=_outbox(2)) == 0
    assert "published=2 failed=0" in capsys.readouterr().out and len(bus.sent) == 2


def test_once_exits_1_when_something_stays_pending() -> None:
    assert run_relay(_args("--once"), {}, publisher=InMemoryPublisher(fail_all=True), outbox=_outbox()) == 1


def test_the_loop_runs_only_while_leader_and_stops_on_the_event() -> None:
    stop = threading.Event()
    entered: list[bool] = []

    @contextmanager
    def leader() -> Iterator[bool]:
        entered.append(True)
        yield True

    class StopAfterFirstPass(InMemoryPublisher):
        def publish(self, **kwargs: Any) -> None:
            super().publish(**kwargs)
            stop.set()

    bus = StopAfterFirstPass()
    code = run_relay(_args("--interval", "0"), {}, publisher=bus, outbox=_outbox(), leader=leader, stop=stop)

    assert code == 0 and len(bus.sent) == 1 and entered == [True]


def test_a_non_leader_publishes_nothing() -> None:
    bus, stop = InMemoryPublisher(), threading.Event()
    calls = 0

    @contextmanager
    def follower() -> Iterator[bool]:
        nonlocal calls
        calls += 1
        if calls == 2:
            stop.set()
        yield False

    assert run_relay(_args("--interval", "0"), {}, publisher=bus, outbox=_outbox(), leader=follower,
                     stop=stop) == 0
    assert bus.sent == []
