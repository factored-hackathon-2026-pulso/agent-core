r"""T-M11-01 en CI: los fixtures committeados (`tests/fixtures/runs/*.yaml`) dan `match` con el motor real.

Además exige que estén vigentes: si el comportamiento del motor cambia, regrábalos con

    uv run agentcore record <camino> --out tests/fixtures/runs/<camino>.yaml \
        --registry tests/fixtures/registry-demo

y revisa el diff (es el cambio observable del motor)."""

from pathlib import Path

import pytest

from agent_core.audit import dump_fixture
from agent_core.cli import main
from testing.replay import SCENARIOS, record_scenario

RUNS = Path("tests/fixtures/runs")
REGISTRY = "tests/fixtures/registry-demo"
CATALOG = "tests/fixtures/catalogo-datos-prueba.yaml"
CAMINOS = ["cancelado", "escalado_por_monto", "interrupcion", "resuelto", "step_up", "uncertain_verify"]


def test_estan_los_seis_caminos_del_flow_de_demo() -> None:
    assert sorted(SCENARIOS) == CAMINOS
    assert sorted(path.stem for path in RUNS.glob("*.yaml")) == CAMINOS


@pytest.mark.parametrize("camino", CAMINOS)
def test_t_m11_01_replay_fixture_da_match(camino: str, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["replay", str(RUNS / f"{camino}.yaml"), "--mode", "fixture", "--registry", REGISTRY,
                 "--catalog", CATALOG])
    assert code == 0 and capsys.readouterr().out.startswith("match")


@pytest.mark.parametrize("camino", CAMINOS)
def test_el_fixture_committeado_esta_vigente(camino: str) -> None:
    grabado = dump_fixture(record_scenario(camino, Path(REGISTRY)))
    assert (RUNS / f"{camino}.yaml").read_text(encoding="utf-8") == grabado, (
        f"{camino}.yaml quedó desactualizado: regrábalo (ver el docstring de este archivo)")
