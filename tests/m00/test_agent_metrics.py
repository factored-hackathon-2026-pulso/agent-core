import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.contracts import render_contracts
from agent_core.domain import SCHEMA_VERSION, Agent
from tests.m01.cases import AGENT

ROOT = Path(__file__).resolve().parents[2]


def metric(mid: str) -> dict[str, Any]:
    return {"id": mid, "description": "d", "role": "monitor", "higher_is_better": False,
            "expr": {"event": "engine.escalated", "aggregation": "count", "window": "scenario"}}


def test_agent_without_metrics_stays_valid() -> None:
    assert Agent.model_validate(deepcopy(AGENT)).metrics == []


def test_agent_carries_its_metrics() -> None:
    agent = Agent.model_validate(deepcopy(AGENT) | {"metrics": [metric("esc_count"), metric("esc_other")]})
    assert [m.id for m in agent.metrics] == ["esc_count", "esc_other"]


def test_agent_rejects_unknown_metric_keys() -> None:
    bad = metric("esc_count") | {"query": "SELECT 1"}
    with pytest.raises(ValidationError):
        Agent.model_validate(deepcopy(AGENT) | {"metrics": [bad]})


def test_agent_caps_metric_count() -> None:
    many = [metric(f"m{i}") for i in range(33)]
    with pytest.raises(ValidationError):
        Agent.model_validate(deepcopy(AGENT) | {"metrics": many})


def test_schema_version_bumped_and_contracts_publish_the_new_types() -> None:
    assert SCHEMA_VERSION == "1.3.0"
    files = render_contracts()
    for name in ("MetricDef", "MetricExpr", "JudgeExpr", "Predicate", "AlertThreshold"):
        json.loads(files[f"schemas/{name}.json"])
    assert "metrics" in json.loads(files["schemas/Agent.json"])["properties"]


def test_committed_contracts_match_the_generated_ones() -> None:
    assert (ROOT / "contracts" / "VERSION").read_text(encoding="utf-8").strip() == SCHEMA_VERSION
