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
    build_fixture,
    chain_events,
    check_chain,
    dump_fixture,
    load_fixture_file,
    verify_transfer_link,
)
from agent_core.audit.replay.ports import RecordedClock
from agent_core.cli import main
from agent_core.domain import EngineEvent, RunStarted
from testing.builders import run_state
from testing.engine_world import transfer_world
from testing.fakes.clock import FakeClock
from testing.replay import build_engine_runner, record_scenario
from testing.replay.scenarios import TEXTO

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


def _a_fixture_pinned_to_another_release() -> Fixture:
    """`disputas-demo` exists in the registry but is not the `prod` release of the entry agent (recepcion)."""
    return load_fixture_file(RUN).model_copy(update={"release": "disputas-demo"})


def test_the_runner_rejects_a_release_that_is_not_the_prod_alias_of_the_entry_agent() -> None:
    runner = build_engine_runner(Path(REGISTRY))
    message = "el alias prod de recepcion es la release recepcion-demo, no disputas-demo"
    with pytest.raises(ValueError, match=message):
        Replayer(runner, FakeClock(), definitions=runner.definitions).replay(
            _a_fixture_pinned_to_another_release(), "fixture")


def test_the_cli_exits_3_on_a_release_that_is_not_the_prod_alias_of_the_entry_agent(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "otra-release.yaml"
    path.write_text(dump_fixture(_a_fixture_pinned_to_another_release()), encoding="utf-8")
    code = main(["replay", str(path), "--mode", "fixture", "--registry", REGISTRY, "--catalog", CATALOG])
    assert code == 3
    assert capsys.readouterr().err.startswith("replay falló: ValueError")


def _transfer_then_one_more_turn(world: Any) -> None:
    """The transfer scenario plus a turn the target answers: its turn id comes after the shared one."""
    world.start()
    world.understands("continue")
    world.routes("disputas")
    world.understands("start_flow", flow="disputa-cargo")
    world.turn(TEXTO)
    world.understands("continue")
    world.matches()
    world.turn("gracias")


def test_a_session_with_a_turn_after_the_transfer_replays_as_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """The target of the transfer repeats the origin's turn id in its chain: the runner deduplicates the turn
    ids before indexing them by op, or the later turn would take the instant of the transfer turn."""
    world = transfer_world(registry_root=Path(REGISTRY), record=True)
    _transfer_then_one_more_turn(world)
    assert world.driver.run_id is not None and world.driver.session_id is not None
    assert world.recording_tools and world.recording_llm
    with world.store.uow() as uow:
        runs = uow.list_runs_by_session(world.driver.session_id)
    origin = world.driver.run_id
    linked = [e for r in runs if r.run_id != origin for e in world.audit.read(r.run_id)]
    turns = [e.turn_id for e in [*world.audit.read(origin), *linked] if e.type == "turn_started"]
    assert len(turns) > len(set(turns)), "the target repeats a turn id of the origin"
    fixture = build_fixture("transferencia-con-turno", origin, world.release.id, world.audit.read(origin),
                            world.driver.ops, world.recording_tools, world.recording_llm, linked=linked)
    entered: list[str | None] = []
    real = RecordedClock.enter_turn

    def spy(self: RecordedClock, turn_id: str | None) -> None:
        entered.append(turn_id)
        real(self, turn_id)

    monkeypatch.setattr(RecordedClock, "enter_turn", spy)
    report = _replay(fixture)
    assert (report.verdict, report.first_divergence) == ("match", None)
    # The recording clock is constant, so the instants cannot tell the turns apart: the ids entered can.
    assert entered == [None, "turn-0002", "turn-0003"]
