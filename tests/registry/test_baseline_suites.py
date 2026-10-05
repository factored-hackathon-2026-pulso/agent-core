"""Las suites base de los cuatro agentes de la prueba E2E (brief A2.4) cargan y no tienen problemas.

Vienen del banco `codex-bank` del motor de mejora (borradores sintéticos, 104 casos). Aquí solo se comprueba
que son artefactos válidos de agent-core: esquema, `agent_id`, una por agente (lo que admite la semilla) y sin
problemas de `suite_problems`. Que sus casos pasen en los agentes lo mide la evaluación sobre `serve`."""

from pathlib import Path

import pytest
import yaml

from agent_core.domain import Agent
from agent_core.registry.suite import EvalSuite, suite_problems

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "registry-e2e"
AGENTS = {"recepcion": 28, "disputas": 28, "consultas": 26, "copiloto-asesor": 22}


def _agent(agent_id: str) -> Agent:
    path = ROOT / "agents" / f"{agent_id}@1.0.0.yaml"
    return Agent.model_validate(yaml.safe_load(path.read_text("utf-8")))


def _suite(agent_id: str) -> EvalSuite:
    path = ROOT / "eval_suites" / f"{agent_id}-suite@1.0.0.yaml"
    return EvalSuite.model_validate(yaml.safe_load(path.read_text("utf-8")))


@pytest.mark.parametrize("agent_id", sorted(AGENTS))
def test_each_fixture_agent_has_a_valid_baseline_suite(agent_id: str) -> None:
    suite = _suite(agent_id)
    assert suite.agent_id == agent_id and suite.id == f"{agent_id}-suite"
    assert len(suite.scenarios) == AGENTS[agent_id]
    assert suite_problems(_agent(agent_id), suite) == []


def test_the_baseline_bank_has_104_cases() -> None:
    assert sum(len(_suite(a).scenarios) for a in AGENTS) == 104


def test_the_seed_carries_exactly_one_suite_per_agent() -> None:
    by_agent: dict[str, list[str]] = {}
    for path in sorted((ROOT / "eval_suites").glob("*.yaml")):
        suite = EvalSuite.model_validate(yaml.safe_load(path.read_text("utf-8")))
        by_agent.setdefault(suite.agent_id, []).append(suite.id)
    assert {a: len(ids) for a, ids in by_agent.items() if a in AGENTS} == dict.fromkeys(AGENTS, 1)


def test_the_advisor_cases_use_the_advisor_principal_with_a_subject() -> None:
    suite = _suite("copiloto-asesor")
    assert {getattr(s, "principal").type for s in suite.scenarios} == {"advisor"}  # noqa: B009
    assert all(getattr(s, "principal").subject is not None for s in suite.scenarios)  # noqa: B009
