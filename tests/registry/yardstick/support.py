from typing import Any

from agent_core.domain import MetricDef
from agent_core.registry.evaluation.suite import EvalSuite
from agent_core.registry.evaluation.yardstick import Yardstick


def metric(mid: str, role: str = "gate", higher: bool = True, event: str = "engine.turn_completed",
           **expr: Any) -> MetricDef:
    body: dict[str, Any] = {"event": event, "aggregation": "count", "window": "scenario"} | expr
    return MetricDef.model_validate(
        {"id": mid, "description": "d", "role": role, "higher_is_better": higher, "expr": body}
    )


def scenario(sid: str, repetitions: int = 1, **over: Any) -> dict[str, Any]:
    source = {"kind": "scripted", "user_turns": ["hola"], "tool_fixtures": {},
              "clock_start": "2026-01-01T00:00:00Z"}
    return {"id": sid, "source": source, "repetitions": repetitions} | over


def thr(noise: str = "0", floor: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"noise_margin": noise}
    if floor is not None:
        out["floor"] = floor
    return out


def suite(scenarios: list[dict[str, Any]], thresholds: dict[str, dict[str, Any]] | None = None,
          agent_id: str = "atencion") -> EvalSuite:
    return EvalSuite.model_validate({"id": "suite", "version": "1.0.0", "agent_id": agent_id,
                                     "scenarios": scenarios, "thresholds": thresholds or {}})


def yardstick(metrics: list[MetricDef], scenarios: list[dict[str, Any]],
              thresholds: dict[str, dict[str, Any]] | None = None) -> Yardstick:
    return Yardstick(metrics=metrics, suite=suite(scenarios, thresholds))
