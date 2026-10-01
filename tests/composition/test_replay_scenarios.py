"""T-M11-01: el replay `fixture` da `match` para cada escenario grabado (los seis caminos del flow de demo y
la sesión con transferencia de la fase 7), cada uno sobre su registro."""

import pytest

from agent_core.audit import Replayer
from testing.engine_world import REGISTRY_DEMO
from testing.fakes.clock import FakeClock
from testing.replay import SCENARIO_REGISTRY, SCENARIOS, build_engine_runner, record_scenario


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_t_m11_01_el_camino_grabado_se_reproduce_en_match(name: str) -> None:
    fixture = record_scenario(name)
    runner = build_engine_runner(SCENARIO_REGISTRY.get(name, REGISTRY_DEMO))
    report = Replayer(runner, FakeClock(), definitions=runner.definitions).replay(fixture, "fixture")
    assert (report.verdict, report.first_divergence) == ("match", None)
