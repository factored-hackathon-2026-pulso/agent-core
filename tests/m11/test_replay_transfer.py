"""Replay of a session that transferred (ADR 0021, phase 7; M11 decisions 9, 13, 14 and 24).

Synthetic data only."""

from pathlib import Path
from typing import Any

import pytest
import yaml

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

def _target_chain(run_id: str = TARGET, agent: str = "disputas@1.0.0", origin: dict[str, Any] | None = None,
                  **origin_changes: Any) -> list[EngineEvent]:
    """A valid (re-chained) target chain; `origin=None` with no changes keeps the true origin."""
    started: dict[str, Any] = {"agent": agent, "mode": "conversational", "principal_type": "customer",
                               "locale": "es"}
    if origin is not None or origin_changes:
        started["origin"] = {**(origin or ORIGIN_PAYLOAD), **origin_changes}
    return chain_events(run_id, [
        event("run_started", run_id=run_id, n=1, release=RELEASE_TARGET, payload=started),
        event("transfer_received", run_id=run_id, n=2, release=RELEASE_TARGET, payload={
            "transfer_id": "transfer-0002", "accepted_slots": ["problema"], "packet_fp": FP}),
        event("turn_completed", run_id=run_id, n=3, release=RELEASE_TARGET),
    ], None)


TARGET_EVENTS = _target_chain(origin=ORIGIN_PAYLOAD)
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
    assert list(yaml.safe_load(text))[-1] == "linked"
    assert load_fixture(text).linked == FIXTURE_WITH_LINKED.linked


@pytest.mark.parametrize("path", sorted(Path("tests/fixtures/runs").glob("*.yaml")), ids=lambda p: p.stem)
def test_committed_fixtures_without_linked_load_and_dump_byte_identical(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    fixture = load_fixture(text)
    assert "linked" not in yaml.safe_load(text) and fixture.linked == []
    assert dump_fixture(fixture) == text


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


def test_the_engine_gets_events_and_linked_and_a_full_replay_matches() -> None:
    cases: list[ReplayCase] = []

    class _Spy(StubEngine):
        def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]:
            cases.append(case)
            return super().run(case, ports)

    report = _replayer(_Spy()).replay(FIXTURE_WITH_LINKED, "fixture")
    assert report.verdict == "match" and report.run_id == ORIGIN and report.release == RELEASE_ORIGIN
    assert [e.event_id for e in cases[0].recorded] == [e.event_id for e in [*ORIGIN_EVENTS, *TARGET_EVENTS]]


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


def test_a_broken_linked_chain_is_chain_broken_and_names_the_run() -> None:
    report = _replayer(_NeverRuns()).replay(_tampered_linked(), "fixture")
    assert report.verdict == "chain_broken" and report.chain_broken_at == 1
    assert report.chain_broken_run == TARGET and report.chain_broken_reason == "hash no coincide"


def test_a_broken_origin_chain_does_not_name_a_linked_run() -> None:
    events = list(ORIGIN_EVENTS)
    events[1] = events[1].model_copy(update={"release": "rel-alterada"})
    fixture = FIXTURE_WITH_LINKED.model_copy(update={"events": events})
    report = _replayer(_NeverRuns()).replay(fixture, "fixture")
    assert report.verdict == "chain_broken" and report.chain_broken_run is None


# I1 — the link between the chains is checked before the engine runs


def _other_origin_hash() -> str:
    """The `turn_completed` hash of another recording with the same ids (another transfer reason)."""
    bare = _origin_bare()
    moved = bare[3]
    bare[3] = moved.model_copy(update={"payload": moved.payload.model_copy(  # type: ignore[attr-defined]
        update={"reason": "otra razón"})})
    other = chain_events(ORIGIN, bare, None)[4].hash
    assert other is not None and other != _LINK_HASH
    return other


_BROKEN_LINKS = {
    "forged_hash": lambda: _target_chain(from_event_hash="0" * 64),
    "swapped_chain": lambda: _target_chain(from_event_hash=_other_origin_hash()),
    "hash_of_another_event": lambda: _target_chain(from_event_hash=ORIGIN_EVENTS[3].hash),
    "rejected_transfer_id": lambda: _target_chain(transfer_id="transfer-0001"),
    "other_from_run": lambda: _target_chain(from_run_id="run-z"),
    "other_from_agent": lambda: _target_chain(from_agent="consultas@1.0.0"),
    "other_from_release": lambda: _target_chain(from_release_id="rel-otra"),
    "other_to_agent": lambda: _target_chain(agent="consultas@1.0.0", origin=ORIGIN_PAYLOAD),
    "not_named_by_to_run_id": lambda: _target_chain(run_id="run-c", origin=ORIGIN_PAYLOAD),
    "same_run_as_origin": lambda: _target_chain(run_id=ORIGIN, origin=ORIGIN_PAYLOAD),
    "no_origin": lambda: _target_chain(),
}


@pytest.mark.parametrize("name", list(_BROKEN_LINKS))
def test_a_linked_chain_that_is_not_linked_to_its_origin_is_not_a_match(name: str) -> None:
    linked = _BROKEN_LINKS[name]()
    fixture = FIXTURE_WITH_LINKED.model_copy(update={"linked": linked})
    report = _replayer(StubEngine()).replay(fixture, "fixture")  # the stub would replay it as `match`
    assert report.verdict == "chain_broken"
    assert report.chain_broken_run == linked[0].run_id and report.chain_broken_at == 0
    assert report.chain_broken_reason is not None


def test_a_valid_second_linked_run_may_come_from_the_first_one() -> None:
    """Depth 2: the origin of a linked run may be an earlier linked run of the session."""
    first = chain_events(TARGET, [
        *(e.model_copy(update={"seq": None, "prev_hash": None, "hash": None}) for e in TARGET_EVENTS[:2]),
        event("run_transferred", run_id=TARGET, n=3, release=RELEASE_TARGET, payload={
            "transfer_id": "transfer-0003", "to_agent": "consultas@1.0.0",
            "to_release_id": "rel-consultas-demo", "to_run_id": "run-c", "reason": "consulta",
            "packet_fp": FP, "directory": "atencion-cliente", "directory_hash": "c" * 64,
            "candidates": ["consultas"]}),
        event("turn_completed", run_id=TARGET, n=4, release=RELEASE_TARGET),
    ], None)
    second = chain_events("run-c", [event("run_started", run_id="run-c", n=1, release="rel-consultas-demo",
                                          turn_id=None, payload={
        "agent": "consultas@1.0.0", "mode": "conversational", "principal_type": "customer", "locale": "es",
        "origin": {"kind": "transfer", "transfer_id": "transfer-0003", "from_run_id": TARGET,
                   "from_agent": "disputas@1.0.0", "from_release_id": RELEASE_TARGET,
                   "from_event_hash": first[3].hash, "depth": 2}})], None)
    fixture = FIXTURE_WITH_LINKED.model_copy(update={"linked": [*first, *second]})
    assert _replayer(StubEngine()).replay(fixture, "fixture").verdict == "match"


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


@pytest.mark.parametrize("field", ["document_number", "first_name"])
def test_a_sha256_hex_in_a_pii_field_is_still_rejected(field: str) -> None:
    with pytest.raises(FixtureRejected):
        check_fixture(_fixture_with_full({field: "a" * 64}), _EMPTY_CATALOG)


@pytest.mark.parametrize("value", [
    "1234567" + "a" * 58,           # 65 characters: not a sha256
    "1234567" + "A" * 57,           # upper case: not how the engine writes hashes
    "hash 1234567" + "a" * 57,      # a hash inside other text
])
def test_only_an_exact_lowercase_sha256_is_exempt(value: str) -> None:
    with pytest.raises(FixtureRejected):
        check_fixture(_fixture_with_full({"hash": value}), _EMPTY_CATALOG)
