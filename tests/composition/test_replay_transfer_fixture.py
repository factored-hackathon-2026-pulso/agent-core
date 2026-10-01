"""Phase 7: the recorded transfer session replays as `match`, is current, and its chains are linked.

Synthetic data only. Re-record with

    uv run agentcore record transferencia --out tests/fixtures/runs-transfer/transferencia.yaml \
        --registry tests/fixtures/registry-transfer-demo --catalog tests/fixtures/catalogo-datos-prueba.yaml
"""

from pathlib import Path
from typing import Any

import pytest

from agent_core.audit import (
    Fixture,
    Replayer,
    chain_events,
    check_chain,
    dump_fixture,
    load_fixture_file,
    verify_transfer_link,
)
from agent_core.cli import main
from agent_core.domain import EngineEvent, RunStarted
from testing.builders import run_state
from testing.fakes.clock import FakeClock
from testing.replay import build_engine_runner, record_scenario

RUN = Path("tests/fixtures/runs-transfer/transferencia.yaml")
REGISTRY = "tests/fixtures/registry-transfer-demo"
CATALOG = "tests/fixtures/catalogo-datos-prueba.yaml"


def test_the_transfer_session_replays_as_match(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["replay", str(RUN), "--mode", "fixture", "--registry", REGISTRY, "--catalog", CATALOG])
    assert code == 0 and capsys.readouterr().out.startswith("match")


def test_the_committed_transfer_fixture_is_current() -> None:
    recorded = dump_fixture(record_scenario("transferencia", Path(REGISTRY)))
    assert RUN.read_text(encoding="utf-8") == recorded, (
        "transferencia.yaml quedó desactualizado: regrábalo con `agentcore record transferencia`")


class _Chains:
    """`AuditSink.read` over the recorded chains (all `verify_transfer_link` needs)."""

    def __init__(self, events: list[EngineEvent]) -> None:
        self._events = events

    def read(self, run_id: str) -> list[EngineEvent]:
        return [e for e in self._events if e.run_id == run_id]


def _target_of(fixture: Fixture) -> Any:
    started = fixture.linked[0]
    assert isinstance(started, RunStarted) and started.payload.origin is not None
    return run_state(run_id=started.run_id, session_id=started.session_id, origin=started.payload.origin)


def test_the_recorded_chains_are_linked_by_hash() -> None:
    fixture = load_fixture_file(RUN)
    assert fixture.linked, "the transfer fixture records the target chain"
    target = _target_of(fixture)
    assert verify_transfer_link(target, _Chains([*fixture.events, *fixture.linked])) == []  # type: ignore[arg-type]


def test_the_fixture_records_two_runs_two_releases_and_the_directory() -> None:
    fixture = load_fixture_file(RUN)
    assert {e.run_id for e in fixture.events} == {fixture.run_id}
    assert len({e.run_id for e in fixture.linked}) == 1
    assert fixture.release == "recepcion-demo"
    assert {e.release for e in fixture.linked} == {"disputas-demo"}
    origin = fixture.linked[0].payload.origin  # type: ignore[attr-defined]
    assert origin.from_run_id == fixture.run_id
    moved = next(e for e in fixture.events if e.type == "run_transferred")
    assert moved.payload.to_run_id == fixture.linked[0].run_id  # type: ignore[attr-defined]
    assert moved.payload.transfer_id == origin.transfer_id  # type: ignore[attr-defined]
    listed = [r.result_full for r in fixture.full.values()
              if isinstance(r.result_full, dict) and "choices" in r.result_full]
    assert [r["choices"] for r in listed] == [["consultas", "disputas"]]  # type: ignore[index]


def _replay(fixture: Fixture) -> Any:
    runner = build_engine_runner(Path(REGISTRY))
    return Replayer(runner, FakeClock(), definitions=runner.definitions).replay(fixture, "fixture")


def test_the_runner_reproduces_both_runs_of_the_session() -> None:
    report = _replay(load_fixture_file(RUN))
    assert (report.verdict, report.first_divergence) == ("match", None)


def _with_forged_link(fixture: Fixture, **origin_changes: Any) -> Fixture:
    """The target chain re-hashed after changing its `origin`: valid on its own, no longer linked."""
    started = fixture.linked[0]
    payload = started.payload  # type: ignore[attr-defined]
    forged = started.model_copy(update={"payload": payload.model_copy(
        update={"origin": payload.origin.model_copy(update=origin_changes)})})
    bare = [e.model_copy(update={"seq": None, "prev_hash": None, "hash": None})
            for e in [forged, *fixture.linked[1:]]]
    linked = chain_events(started.run_id, bare, None)
    return fixture.model_copy(update={"linked": linked})


@pytest.mark.parametrize(("changes", "reason"), [
    ({"from_event_hash": "0" * 64}, "el hash de origen no está en la cadena de origen"),
    ({"transfer_id": "transfer-forjada"}, "no hay un run_transferred que apunte a este run"),
    ({"from_release_id": "disputas-demo"}, "el agente o la release de origen no coinciden"),
], ids=["from_event_hash", "transfer_id", "from_release_id"])
def test_a_tampered_link_in_the_recorded_fixture_is_detected(changes: dict[str, str], reason: str) -> None:
    fixture = _with_forged_link(load_fixture_file(RUN), **changes)
    assert check_chain(fixture.linked[0].run_id, list(fixture.linked)).ok, "the forged chain holds on its own"
    target = _target_of(fixture)
    assert verify_transfer_link(target, _Chains([*fixture.events, *fixture.linked])) != []  # type: ignore[arg-type]
    report = _replay(fixture)
    assert report.verdict == "chain_broken"
    assert report.chain_broken_run == fixture.linked[0].run_id
    assert report.chain_broken_reason is not None and report.chain_broken_reason.startswith(reason)


def test_a_tampered_target_event_breaks_its_chain() -> None:
    fixture = load_fixture_file(RUN)
    linked = list(fixture.linked)
    started = linked[0]
    payload = started.payload  # type: ignore[attr-defined]
    linked[0] = started.model_copy(update={"payload": payload.model_copy(
        update={"origin": payload.origin.model_copy(update={"depth": 2})})})
    report = _replay(fixture.model_copy(update={"linked": linked}))
    assert (report.verdict, report.chain_broken_run) == ("chain_broken", started.run_id)


def test_the_runner_rejects_a_release_the_registry_does_not_have() -> None:
    fixture = load_fixture_file(RUN).model_copy(update={"release": "no-existe"})
    runner = build_engine_runner(Path(REGISTRY))
    with pytest.raises(ValueError, match="no-existe"):
        Replayer(runner, FakeClock(), definitions=runner.definitions).replay(fixture, "fixture")
