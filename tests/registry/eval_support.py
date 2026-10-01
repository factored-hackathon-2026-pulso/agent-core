"""Synthetic builders for the suite, yardstick and gate tests (ADR 0020)."""

from typing import Any

from agent_core.domain import MetricDef
from agent_core.registry.suite import EvalSuite


def metric(mid: str, role: str = "gate", higher: bool = True, event: str = "engine.turn_completed",
           **expr: Any) -> MetricDef:
    body: dict[str, Any] = {"event": event, "aggregation": "count", "window": "scenario"} | expr
    return MetricDef.model_validate(
        {"id": mid, "description": "d", "role": role, "higher_is_better": higher, "expr": body})


def scenario(sid: str, repetitions: int | None = 1, **over: Any) -> dict[str, Any]:
    """`scripted` scenario in `main`'s format; `repetitions=None` inherits the suite's."""
    body: dict[str, Any] = {"id": sid, "principal": {"id": "cust-001"},
                            "steps": [{"op": "start"}, {"op": "turn", "text": "hola"}]}
    if repetitions is not None:
        body["repetitions"] = repetitions
    return body | over


def thr(noise: str = "0", floor: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"noise_margin": noise}
    if floor is not None:
        out["floor"] = floor
    return out


def suite(scenarios: list[dict[str, Any]], thresholds: dict[str, dict[str, Any]] | None = None,
          agent_id: str = "atencion", **over: Any) -> EvalSuite:
    data: dict[str, Any] = {"id": "suite", "version": "1.0.0", "agent_id": agent_id, "repetitions": 1,
                            "scenarios": scenarios, "thresholds": thresholds or {}}
    return EvalSuite.model_validate(data | over)
