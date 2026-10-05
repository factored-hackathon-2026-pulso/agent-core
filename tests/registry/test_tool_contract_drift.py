"""Las ToolDefs de las fixtures no se desvían del contrato del tool-service (brief A6).

El tool-service es dueño de lo que ejecuta: `source`, `args_schema`, riesgo, nivel de autenticación,
idempotencia y lectura posterior. `tests/fixtures/tool-service-contract/` es una copia de su `registry/tools`
(se refresca con `scripts/sync_tool_contract.py`); las fixtures del registry de la prueba E2E deben decir lo
mismo de cada tool que comparten. Lo que no está en el contrato tiene que ser una tool que sirve el motor."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_core.composition.engine_tools import ENGINE_TOOL_IDS

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "tests" / "fixtures" / "tool-service-contract" / "tools"
FIXTURES = ROOT / "tests" / "fixtures" / "registry-e2e" / "tools"


def _sibling() -> Path:
    """El checkout de tool-service junto a agent-core (también desde un worktree de `.claude/worktrees`)."""
    for parent in ROOT.parents:
        if (parent / "tool-service" / "registry" / "tools").is_dir():
            return parent / "tool-service" / "registry" / "tools"
    return ROOT / "no-hay-tool-service"


SIBLING = _sibling()
# `description` es prosa: puede diferir sin cambiar lo que la tool hace.
COMPARED = ("id", "version", "risk_class", "min_auth_level", "idempotent", "readback_by", "source",
            "args_schema")


def _load(directory: Path) -> dict[str, dict[str, Any]]:
    return {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.yaml"))}


def test_the_contract_snapshot_is_not_empty() -> None:
    assert len(_load(CONTRACT)) >= 6


@pytest.mark.parametrize("name", sorted(set(_load(CONTRACT)) & set(_load(FIXTURES))))
def test_a_shared_tool_says_the_same_as_the_contract(name: str) -> None:
    contract, fixture = _load(CONTRACT)[name], _load(FIXTURES)[name]
    drift = {k: (contract.get(k), fixture.get(k)) for k in COMPARED
             if contract.get(k) != fixture.get(k)}
    assert not drift, f"{name} se desvía del tool-service: {drift}"


def test_a_fixture_tool_outside_the_contract_is_one_the_engine_serves() -> None:
    outside = {name.split("@")[0] for name in set(_load(FIXTURES)) - set(_load(CONTRACT))}
    assert outside <= ENGINE_TOOL_IDS, f"sin contrato ni servidas por el motor: {outside - ENGINE_TOOL_IDS}"


def test_every_read_tool_with_a_data_source_in_the_contract_declares_it() -> None:
    for name, doc in _load(FIXTURES).items():
        if name in _load(CONTRACT) and _load(CONTRACT)[name].get("source"):
            assert doc.get("source"), f"{name} perdió su `source`"


@pytest.mark.skipif(not SIBLING.is_dir(), reason="no hay un checkout de tool-service junto a agent-core")
def test_the_snapshot_is_current_with_the_tool_service_checkout() -> None:
    assert _load(CONTRACT) == _load(SIBLING), "refresca con scripts/sync_tool_contract.py"
