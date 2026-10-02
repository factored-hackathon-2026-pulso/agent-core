from copy import deepcopy
from typing import Any

from agent_core.domain import EntityKind, ModelProfile
from agent_core.flows.agent import validate_agent
from agent_core.flows.violations import Violation
from tests.m01.cases import ENTITIES, agent, registry

# Origen que `validate_agent` pone en las rutas: el archivo del agente, o la ruta por defecto en memoria.
WHERE = registry().source(EntityKind.agent, "atencion", "1.0.0") or "agents/atencion@1.0.0.yaml"


def _profile_ref() -> str:
    profile = next(e for e in ENTITIES if isinstance(e, ModelProfile))
    return f"{profile.id}@{profile.version}"


def metric(mid: str = "esc_count", **expr_over: Any) -> dict[str, Any]:
    expr = {"event": "engine.escalated", "aggregation": "count", "window": "scenario"} | expr_over
    return {"id": mid, "description": "d", "role": "monitor", "higher_is_better": False, "expr": expr}


def judge(mid: str = "quality", **over: Any) -> dict[str, Any]:
    expr = {"judge_profile": _profile_ref(), "rubric": "r", "target_event": "registry.evaluated"} | over
    return {"id": mid, "description": "d", "role": "gate", "higher_is_better": True, "expr": expr}


def mt(metrics: list[dict[str, Any]]) -> list[Violation]:
    found = validate_agent(agent(metrics=deepcopy(metrics)), registry())
    return [v for v in found if v.rule.startswith("MT-")]


def test_valid_metrics_have_no_mt_violations() -> None:
    assert mt([
        metric("esc_count"),
        metric("p95_turn", aggregation="percentile", field="duration_ms", percentile=95,
               event="engine.turn_completed"),
        metric("cost", aggregation="sum", field="llm.cost_usd", event="engine.response_emitted",
               where=[{"field": "fallback_used", "op": "eq", "value": False}], group_by=["release"]),
        metric("proposals", event="registry.proposal_created",
               where=[{"field": "origin", "op": "in", "value": ["auto_detect", "builder_chat"]}]),
        judge(),
    ]) == []


def test_agent_without_metrics_is_unaffected() -> None:
    assert mt([]) == []


# T-EVAL-01
def test_mt_01_event_outside_the_catalog() -> None:
    found = mt([metric(event="engine.nope")])
    assert [(v.rule, v.path) for v in found] == [("MT-01", f"{WHERE}#/metrics/0/expr/event")]


def test_mt_02_unknown_fields() -> None:
    found = mt([metric(where=[{"field": "nope", "op": "eq", "value": "x"}], group_by=["also_nope"])])
    assert sorted(v.path or "" for v in found) == [
        f"{WHERE}#/metrics/0/expr/group_by/0",
        f"{WHERE}#/metrics/0/expr/where/0/field",
    ]
    assert {v.rule for v in found} == {"MT-02"}


def test_mt_02_aggregated_field_must_be_numeric() -> None:
    found = mt([metric(aggregation="sum", field="reason_code")])
    assert [(v.rule, (v.path or "").rsplit("/", 1)[-1]) for v in found] == [("MT-02", "field")]


def test_mt_02_value_type_and_ordering() -> None:
    wrong_type = mt([metric(where=[{"field": "reason_code", "op": "eq", "value": 3}])])
    ordering_on_text = mt([metric(where=[{"field": "reason_code", "op": "gt", "value": "a"}])])
    bad_in = mt([metric(where=[{"field": "reason_code", "op": "in", "value": ["a", 1]}])])
    assert [v.rule for v in wrong_type + ordering_on_text + bad_in] == ["MT-02", "MT-02", "MT-02"]


def test_mt_02_numeric_comparison_is_accepted_on_numeric_fields() -> None:
    where = [{"field": "duration_ms", "op": "gt", "value": 100}]
    assert mt([metric(event="engine.turn_completed", where=where)]) == []


def test_mt_02_checks_the_rate_denominator() -> None:
    denominator = {"event": "engine.turn_completed", "aggregation": "count", "window": "scenario",
                   "where": [{"field": "nope", "op": "eq", "value": "x"}]}
    found = mt([metric(aggregation="rate", denominator=denominator)])
    assert [v.rule for v in found] == ["MT-02"]
    assert (found[0].path or "").endswith("/denominator/where/0/field")


# MT-03 y Review Focus 4
def test_mt_03_duplicate_metric_ids() -> None:
    found = mt([metric("same"), metric("same", event="engine.run_closed")])
    assert [(v.rule, v.path) for v in found] == [("MT-03", f"{WHERE}#/metrics/1/id")]


def test_mt_04_judge_target_event_outside_the_catalog() -> None:
    found = mt([judge(target_event="engine.nope")])
    assert [v.rule for v in found] == ["MT-04"]


# T-EVAL-10 (parte estructural): los ids de plataforma están reservados
def test_mt_05_platform_ids_are_reserved() -> None:
    found = mt([metric("platform_pii_leak")])
    assert [(v.rule, v.path) for v in found] == [("MT-05", f"{WHERE}#/metrics/0/id")]


def test_mt_06_judge_profile_must_exist() -> None:
    found = mt([judge(judge_profile="no_existe@1.0.0")])
    assert [v.rule for v in found] == ["MT-06"]


# Review Focus 5: entradas enormes o con caracteres raros no rompen M1 ni producen mensajes sin acotar
def test_huge_and_odd_strings_are_clipped() -> None:
    odd = "ñ‮\x00" + "x" * 5000
    found = mt([metric(event=odd[:80], where=[{"field": odd[:80], "op": "eq", "value": odd}])])
    assert found
    assert all(len(v.message) <= 240 and len(v.path or "") <= 240 for v in found)


def test_validate_agent_still_reports_other_rules() -> None:
    found = validate_agent(agent(entry_flow="no_existe@1", metrics=[metric(event="engine.nope")]), registry())
    assert {"G0-02", "MT-01"} <= {v.rule for v in found}
