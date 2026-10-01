"""The phase 7 demo registry (ADR 0021): valid for M1, importable into the registry, coherent contracts."""

from pathlib import Path

import pytest

from agent_core.cli import main
from agent_core.composition import DIRECTORY_TOOL
from agent_core.decision.calibration.artifact import CalibrationArtifact, DirectoryCalibrationSource
from agent_core.domain import Agent, CollectNode, EntityKind, Flow, TransferNode, packet_problem
from agent_core.flows import load_registry
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.service import RegistryService
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.registry.helpers import admin
from tests.registry.service_world import FakeEvaluator

ROOT = Path("tests/fixtures/registry-transfer-demo")
DIRECTORY = "atencion-cliente"
SPECIALISTS = ["consultas", "disputas"]  # codepoint order, as the directory lists them


def _registry():  # type: ignore[no-untyped-def]
    reg, violations = load_registry(ROOT)
    assert violations == []
    return reg


def test_agentcore_validate_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(ROOT)]) == 0, capsys.readouterr().out


def test_the_directory_tool_file_is_the_definition_composition_serves() -> None:
    assert _registry().get_exact(EntityKind.tool, "directory/list", "1.0.0") == DIRECTORY_TOOL


def test_the_specialists_carry_a_card_of_the_directory_and_a_contract() -> None:
    reg = _registry()
    for agent_id in SPECIALISTS:
        agent = reg.get_exact(EntityKind.agent, agent_id, "1.0.0")
        assert isinstance(agent, Agent) and agent.routing is not None and agent.accepts is not None
        assert agent.routing.directory == DIRECTORY and agent.understand is not None
    reception = reg.get_exact(EntityKind.agent, "recepcion", "1.0.0")
    assert isinstance(reception, Agent) and reception.routing is None and reception.accepts is None


def test_the_reception_packet_fits_every_specialist_contract() -> None:
    """REL-T1 by hand for this registry (F9): REL-T1 itself is phase 8."""
    reg = _registry()
    flow = reg.get_exact(EntityKind.flow, "recepcion", "1.0.0")
    assert isinstance(flow, Flow)
    [transfer] = [n for n in flow.nodes if isinstance(n, TransferNode)]
    collected = {n.config.slot for n in flow.nodes if isinstance(n, CollectNode)}
    assert set(transfer.config.packet.slots) <= collected
    # a collect without validator holds a string
    sample = {name: "texto sintético" for name in transfer.config.packet.slots}
    for agent_id in SPECIALISTS:
        agent = reg.get_exact(EntityKind.agent, agent_id, "1.0.0")
        assert isinstance(agent, Agent) and agent.accepts is not None
        assert packet_problem(agent.accepts, sample) is None, agent_id


def test_the_hand_made_calibration_has_the_wildcard_and_is_canonical() -> None:
    source = DirectoryCalibrationSource(ROOT / "calibrations")
    artifact = source.get("cal-transfer-demo")
    assert artifact is not None
    assert artifact.thresholds[("choice", "*", "classifier", "es")] == 0.8
    text = (ROOT / "calibrations" / "cal-transfer-demo.json").read_text(encoding="utf-8")
    assert CalibrationArtifact.from_json(text).to_json() == text.rstrip("\n")


def test_every_decision_model_reads_the_hand_made_artifact() -> None:
    from agent_core.domain import DecisionModelDef

    models = [m for m in _registry().all(EntityKind.decision_model) if isinstance(m, DecisionModelDef)]
    assert {m.thresholds_from for m in models} == {"cal-transfer-demo"}


def test_registry_import_publishes_one_release_per_agent() -> None:
    store = InMemoryRegistryStore()
    service = RegistryService(store, FakeEvaluator(), FakeClock(), FakeIds())
    details = service.import_seed(admin(), ROOT)
    assert len(details) == 3
    with store.transaction() as tx:
        aliased = sorted(agent for agent, _ in tx.aliases_named("prod"))
        assert aliased == ["consultas", "disputas", "recepcion"]


def test_the_registry_directory_lists_the_two_specialists() -> None:
    from agent_core.registry import PostgresRegistry, RegistryDirectory

    store = InMemoryRegistryStore()
    RegistryService(store, FakeEvaluator(), FakeClock(), FakeIds()).import_seed(admin(), ROOT)
    runtime = PostgresRegistry(store, FakeClock())  # type: ignore[arg-type]  # works over any RegistryStore
    members = RegistryDirectory(store, runtime, runtime.release).members(DIRECTORY)
    assert [agent.id for _, agent in members] == SPECIALISTS
