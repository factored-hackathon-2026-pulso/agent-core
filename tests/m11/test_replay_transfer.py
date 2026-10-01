"""Replay of a session that transferred (ADR 0021, phase 7; M11 decisions 9, 13, 14 and 24).

Synthetic data only."""

from typing import Any

import pytest

from agent_core.audit import dump_fixture, load_fixture
from agent_core.audit.chain import chain_events
from agent_core.audit.replay.compare import first_divergence, normalize
from agent_core.audit.replay.fixture import Fixture, FullToolResult
from agent_core.audit.replay.ports import RecordedIds, RecordedPorts
from agent_core.audit.replay.runner import ReplayCase, Replayer
from agent_core.audit.replay.synthetic import FixtureRejected, SyntheticCatalog, check_fixture
from agent_core.domain import EngineEvent
from agent_core.ports import IdKind
from testing.fakes.clock import FakeClock
from tests.m00.samples import FP
from tests.m11.engine_stub import StubEngine
from tests.m11.helpers import event

ORIGIN, TARGET = "run-a", "run-b"
RELEASE_ORIGIN, RELEASE_TARGET = "rel-recepcion-demo", "rel-disputas-demo"


def _origin_bare() -> list[EngineEvent]:
    def ev(kind: str, n: int, **over: Any) -> EngineEvent:
        return event(kind, run_id=ORIGIN, n=n, release=RELEASE_ORIGIN, **over)

    return [
        ev("run_started", 1, turn_id=None, payload={
            "agent": "recepcion@1.0.0", "mode": "conversational", "principal_type": "customer",
            "locale": "es"}),
        ev("turn_started", 2),
        ev("transfer_rejected", 3, payload={
            "transfer_id": "transfer-0001", "to_agent": None, "reason_code": "not_in_directory",
            "directory": "atencion-cliente", "directory_hash": "c" * 64}),
        ev("run_transferred", 4, payload={
            "transfer_id": "transfer-0002", "to_agent": "disputas@1.0.0", "to_release_id": RELEASE_TARGET,
            "to_run_id": TARGET, "reason": "disputa de un cargo", "packet_fp": FP,
            "directory": "atencion-cliente", "directory_hash": "c" * 64, "candidates": ["disputas"]}),
        ev("turn_completed", 5),
        ev("run_closed", 6, payload={"outcome": "resolved", "closed_by": "transfer"}),
    ]


ORIGIN_EVENTS = chain_events(ORIGIN, _origin_bare(), None)
ORIGIN_STARTED, REJECTED, TRANSFERRED = ORIGIN_EVENTS[0], ORIGIN_EVENTS[2], ORIGIN_EVENTS[3]
_LINK_HASH = ORIGIN_EVENTS[4].hash
ORIGIN_PAYLOAD = {"kind": "transfer", "transfer_id": "transfer-0002", "from_run_id": ORIGIN,
                  "from_agent": "recepcion@1.0.0", "from_release_id": RELEASE_ORIGIN,
                  "from_event_hash": _LINK_HASH, "depth": 1}

TARGET_EVENTS = chain_events(TARGET, [
    event("run_started", run_id=TARGET, n=1, release=RELEASE_TARGET, payload={
        "agent": "disputas@1.0.0", "mode": "conversational", "principal_type": "customer", "locale": "es",
        "origin": ORIGIN_PAYLOAD}),
    event("transfer_received", run_id=TARGET, n=2, release=RELEASE_TARGET, payload={
        "transfer_id": "transfer-0002", "accepted_slots": ["problema"], "packet_fp": FP}),
    event("turn_completed", run_id=TARGET, n=3, release=RELEASE_TARGET),
], None)
TARGET_STARTED = TARGET_EVENTS[0]

FIXTURE_WITHOUT_LINKED = Fixture(name="sin-transferencia", run_id=ORIGIN, release=RELEASE_ORIGIN, inputs=[],
                                 events=ORIGIN_EVENTS, full={}, drafts=[])  # type: ignore[arg-type]
FIXTURE_WITH_LINKED = FIXTURE_WITHOUT_LINKED.model_copy(
    update={"name": "transferencia", "linked": TARGET_EVENTS})


def _tampered_linked() -> Fixture:
    linked = list(TARGET_EVENTS)
    received = linked[1]
    linked[1] = received.model_copy(update={"payload": received.payload.model_copy(  # type: ignore[attr-defined]
        update={"accepted_slots": ["otro"]})})
    return FIXTURE_WITH_LINKED.model_copy(update={"linked": linked})


class _NeverRuns:
    def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]:
        raise AssertionError("a broken chain must stop the replay before the engine runs")


def _replayer(engine: Any) -> Replayer:
    return Replayer(engine, FakeClock(), definitions=lambda r: None)  # type: ignore[arg-type, return-value]


def _with_origin(**changes: Any) -> EngineEvent:
    payload = TARGET_STARTED.payload  # type: ignore[attr-defined]
    return TARGET_STARTED.model_copy(update={"payload": payload.model_copy(
        update={"origin": payload.origin.model_copy(update=changes)})})


# 1a — ids


def test_recorded_ids_hand_out_the_target_run_and_the_transfer_ids() -> None:
    ids = RecordedIds.from_events([ORIGIN_STARTED, REJECTED, TRANSFERRED])
    assert [ids.new_id(IdKind.run), ids.new_id(IdKind.run)] == [ORIGIN, TARGET]
    assert [ids.new_id(IdKind.transfer), ids.new_id(IdKind.transfer)] == ["transfer-0001", "transfer-0002"]


def test_recorded_ids_over_both_chains_do_not_repeat_the_target_run() -> None:
    ids = RecordedIds.from_events([*ORIGIN_EVENTS, *TARGET_EVENTS])
    assert [ids.new_id(IdKind.run) for _ in range(3)] == [ORIGIN, TARGET, "run-replay-0001"]
    assert [ids.new_id(IdKind.transfer) for _ in range(3)] == ["transfer-0001", "transfer-0002",
                                                               "transfer-replay-0001"]


# 1b — fixture format


def test_a_fixture_without_linked_chains_dumps_as_before() -> None:
    text = dump_fixture(FIXTURE_WITHOUT_LINKED)
    assert "linked" not in text and load_fixture(text).linked == []


def test_a_fixture_with_linked_chains_round_trips() -> None:
    text = dump_fixture(FIXTURE_WITH_LINKED)
    assert text.rstrip().splitlines()[-1].startswith("  ")  # `linked` is the last key
    assert load_fixture(text).linked == FIXTURE_WITH_LINKED.linked


# 1c — comparison


def test_from_event_hash_is_not_compared_but_the_rest_of_origin_is() -> None:
    other_hash = _with_origin(from_event_hash="f" * 64)
    assert first_divergence([TARGET_STARTED], [other_hash]) is None
    for change in ({"transfer_id": "transfer-0009"}, {"from_run_id": "run-z"}, {"depth": 2}):
        assert first_divergence([TARGET_STARTED], [_with_origin(**change)]) is not None, change


def test_normalize_keeps_a_run_started_without_origin_untouched() -> None:
    data = normalize(ORIGIN_STARTED)
    assert "origin" not in data["payload"]  # type: ignore[operator]


# 1c/1d — replayer over events + linked


def test_the_replayer_runs_and_compares_over_events_and_linked() -> None:
    engine = StubEngine()
    report = _replayer(engine).replay(FIXTURE_WITH_LINKED, "fixture")
    assert report.verdict == "match" and report.run_id == ORIGIN and report.release == RELEASE_ORIGIN


def test_a_change_in_a_linked_event_diverges() -> None:
    def flip(e: EngineEvent) -> EngineEvent:
        if e.run_id == TARGET and getattr(e, "type", "") == "transfer_received":
            return e.model_copy(update={"payload": e.payload.model_copy(  # type: ignore[attr-defined]
                update={"accepted_slots": []})})
        return e

    report = _replayer(StubEngine(flip)).replay(FIXTURE_WITH_LINKED, "fixture")
    assert report.verdict == "diverged" and report.first_divergence is not None
    assert report.first_divergence.expected["run_id"] == TARGET  # type: ignore[index]


def test_a_missing_linked_run_in_the_produced_events_diverges() -> None:
    class _OriginOnly:
        def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]:
            return [e for e in case.recorded if e.run_id == ORIGIN]

    assert _replayer(_OriginOnly()).replay(FIXTURE_WITH_LINKED, "fixture").verdict == "diverged"


def test_a_broken_linked_chain_is_chain_broken() -> None:
    report = _replayer(_NeverRuns()).replay(_tampered_linked(), "fixture")
    assert report.verdict == "chain_broken" and report.chain_broken_at == 1


def test_recorded_ports_serve_the_ids_of_both_chains() -> None:
    seen: list[str] = []

    class _AsksIds(StubEngine):
        def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]:
            seen.extend([ports.ids.new_id(IdKind.run), ports.ids.new_id(IdKind.run)])
            return super().run(case, ports)

    assert _replayer(_AsksIds()).replay(FIXTURE_WITH_LINKED, "fixture").verdict == "match"
    assert seen == [ORIGIN, TARGET]


# 1e — sha256 exemption (Question 2)

_EMPTY_CATALOG = SyntheticCatalog(email_domains=["example.test"], numbers=[], values=[])


def _fixture_with_full(result: dict[str, Any]) -> Fixture:
    return FIXTURE_WITHOUT_LINKED.model_copy(
        update={"full": {"call-0001": FullToolResult(status="ok", result_full=result)}})


def test_a_sha256_hex_leaf_in_full_is_not_a_number() -> None:
    check_fixture(_fixture_with_full({"hash": "1234567" + "a" * 57}), _EMPTY_CATALOG)  # does not raise


@pytest.mark.parametrize("value", [
    "1234567" + "a" * 58,           # 65 characters: not a sha256
    "1234567" + "A" * 57,           # upper case: not how the engine writes hashes
    "hash 1234567" + "a" * 57,      # a hash inside other text
])
def test_only_an_exact_lowercase_sha256_is_exempt(value: str) -> None:
    with pytest.raises(FixtureRejected):
        check_fixture(_fixture_with_full({"hash": value}), _EMPTY_CATALOG)
