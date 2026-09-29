from collections.abc import Callable, Iterable
from typing import Any

from agent_core.domain import EntityKind, Flow, RefSpec, RegistryEntity, RuleNode
from agent_core.domain.nodes import RuleConfig
from agent_core.flows.context import Ctx
from agent_core.flows.rules.phase5 import g0_07, g0_08, g0_10, g0_11, g0_13, g0_14, g0_15, g0_16
from agent_core.flows.violations import Violation
from tests.m01.cases import base, check, node, registry, rules, task_base

PHASE5 = (g0_07, g0_08, g0_10, g0_11, g0_13, g0_14, g0_15, g0_16)


def _with_rule(expr: Any) -> dict[str, Any]:
    d = base()
    node(d, "buscar")["next"]["ok"] = "r"
    d["nodes"].append(
        {
            "id": "r",
            "type": "rule",
            "config": {"expr": expr},
            "next": {"true": "confirmar", "false": "confirmar"},
        }
    )
    return d


def _with_decide(model: str) -> dict[str, Any]:
    d = base()
    node(d, "buscar")["next"]["ok"] = "elige"
    d["nodes"].append(
        {
            "id": "elige",
            "type": "decide",
            "config": {"model": model, "branch_on": "campo", "save_as": "d"},
            "next": {"a": "confirmar", "b": "confirmar", "low_confidence": "esc"},
        }
    )
    return d


def _direct(rule: Callable[[Ctx], Iterable[Violation]], d: dict[str, Any]) -> list[Violation]:
    return list(rule(Ctx.build(Flow.model_validate(d), registry())))


def _deep(levels: int, leaf: Any) -> Any:
    expr = leaf
    for _ in range(levels):
        expr = {"!": [expr]}
    return expr


def _unvalidated_rule_flow(expr: Any) -> Flow:
    """Un flow al que el esquema (G0-01) no dejaría pasar: se construye sin validar."""
    flow = Flow.model_validate(base())
    bad = RuleNode.model_construct(
        id="r",
        type="rule",
        config=RuleConfig.model_construct(policy=None, expr=expr),
        next={"true": "confirmar", "false": "confirmar"},
    )
    return flow.model_copy(update={"nodes": [*flow.nodes, bad]})


def test_base_still_valid() -> None:
    assert check(base()) == []


# T-M1-07 (regla de producción, probada directo)
def test_g0_07_agent_node_with_write_tool() -> None:
    d = base()
    d["nodes"].append(
        {
            "id": "ag",
            "type": "agent",
            "config": {"tools_allowed": ["escribir@1"], "max_steps": 3, "prompt_ref": "p/gen", "goal": "x"},
        }
    )
    found = list(g0_07(Ctx.build(Flow.model_validate(d), registry())))
    assert [v.rule for v in found] == ["G0-07"]
    assert found[0].node_id == "ag"
    assert found[0].path == "/nodes/9/config/tools_allowed/0"


def test_g0_07_agent_node_with_read_and_compute_tools_is_valid() -> None:
    d = base()
    d["nodes"].append(
        {
            "id": "ag",
            "type": "agent",
            "config": {
                "tools_allowed": ["leer@1", "calc@1"],
                "max_steps": 3,
                "prompt_ref": "p/gen",
                "goal": "x",
            },
        }
    )
    assert _direct(g0_07, d) == []


def test_g0_07_unresolved_tool_is_left_to_g0_02() -> None:
    d = base()
    d["nodes"].append(
        {
            "id": "ag",
            "type": "agent",
            "config": {"tools_allowed": ["nada@1"], "max_steps": 3, "prompt_ref": "p/gen", "goal": "x"},
        }
    )
    assert _direct(g0_07, d) == []


# T-M1-08, T-M1-25
def test_business_literal_in_rule() -> None:
    assert rules(check(_with_rule({">": [{"var": "facts.datos.value.n"}, 500]}))) == {"G0-08"}
    assert rules(check(_with_rule({">": [{"var": "facts.datos.value.n"}, 0]}))) == {"G0-08"}


def test_rule_without_literals_is_valid() -> None:
    expr = {"and": [{"==": [{"var": "slots.desc"}, None]}, {"==": [{"var": "facts.datos.value.ok"}, True]}]}
    assert check(_with_rule(expr)) == []


def test_enum_value_of_collect_is_allowed() -> None:
    d = _with_rule({"==": [{"var": "slots.desc"}, "alta"]})
    node(d, "pedir")["config"]["validator"] = {"kind": "enum", "value": ["alta", "baja"]}
    assert check(d) == []
    d_in = _with_rule({"in": [{"var": "slots.desc"}, ["alta", "otra"]]})
    node(d_in, "pedir")["config"]["validator"] = {"kind": "enum", "value": ["alta", "baja"]}
    assert rules(check(d_in)) == {"G0-08"}


def test_string_literal_without_enum_is_a_violation_and_message_is_clipped() -> None:
    long_text = "x" * 500
    found = check(_with_rule({"==": [{"var": "slots.desc"}, long_text]}))
    assert rules(found) == {"G0-08"}
    assert all(len(v.message) < 200 for v in found)


def test_var_default_is_a_literal() -> None:
    assert rules(check(_with_rule({"==": [{"var": ["slots.desc", "x"]}, None]}))) == {"G0-08"}


def test_g0_08_messages_are_sorted_by_position() -> None:
    d = _with_rule(
        {"and": [{">": [{"var": "facts.datos.value.n"}, 5]}, {"<": [{"var": "facts.datos.value.n"}, 9]}]}
    )
    found = check(d)
    assert [v.path for v in found] == sorted(v.path or "" for v in found)
    assert len(found) == 2


def test_g0_08_overdeep_expression_fails_closed() -> None:
    # El literal 500 queda más allá de MAX_DEPTH: `expr_literals` lo omitiría en silencio.
    flow = _unvalidated_rule_flow(_deep(100, {">": [{"var": "facts.datos.value.n"}, 500]}))
    found = list(g0_08(Ctx.build(flow, registry())))
    assert [v.rule for v in found] == ["G0-08"]
    assert "profundidad" in found[0].message
    assert found[0].node_id == "r"


def test_g0_08_overdeep_without_literals_also_fails_closed() -> None:
    flow = _unvalidated_rule_flow(_deep(100, {"var": "slots.desc"}))
    assert [v.rule for v in g0_08(Ctx.build(flow, registry()))] == ["G0-08"]


def test_g0_10_overdeep_expression_fails_closed() -> None:
    flow = _unvalidated_rule_flow(_deep(100, {"var": "decisions.d.campo"}))
    found = list(g0_10(Ctx.build(flow, registry())))
    assert [v.rule for v in found] == ["G0-10"]
    assert "profundidad" in found[0].message


def test_g0_08_in_flow_with_duplicate_ids_and_missing_nodes_does_not_raise() -> None:
    d = _with_rule({">": [{"var": "facts.datos.value.n"}, 5]})
    d["nodes"].append(
        {
            "id": "r",
            "type": "rule",
            "config": {"expr": {">": [{"var": "slots.desc"}, 7]}},
            "next": {"true": "nada", "false": "nada"},
        }
    )
    found = _direct(g0_08, d)
    assert [v.rule for v in found] == ["G0-08", "G0-08"]


# T-M1-10
def test_decisions_in_read_tool() -> None:
    d = base()
    node(d, "buscar")["config"]["args"] = {"q": "decisions.d.campo"}
    assert rules(check(d)) == {"G0-10"}


def test_decisions_in_compute_tool_are_allowed() -> None:
    d = base()
    node(d, "buscar")["config"]["tool"] = "calc@1"
    node(d, "buscar")["config"]["args"] = {"q": ["decisions.d.campo", "facts.datos.value.x", "literal"]}
    assert check(d) == []


# T-M1-39
def test_rule_reading_decisions() -> None:
    assert rules(check(_with_rule({"==": [{"var": "decisions.d.campo"}, None]}))) == {"G0-10"}


def test_confirm_args_with_decisions() -> None:
    d = base()
    node(d, "confirmar")["config"]["action"]["args"] = {"q": "decisions.d.campo"}
    assert rules(check(d)) == {"G0-10"}


def test_template_reading_decisions() -> None:
    d = base()
    node(d, "ok_msg")["config"]["template_ref"] = "t/lee_dec"
    assert rules(check(d)) == {"G0-10"}


def test_whole_fact_outside_allowed_facts() -> None:
    d = base()
    node(d, "buscar")["config"]["args"] = {"q": "facts.datos"}
    assert rules(check(d)) == {"G0-10"}


def test_whole_fact_in_allowed_facts_is_valid() -> None:
    d = base()
    node(d, "ok_msg")["config"] = {
        "generate": {
            "prompt_ref": "p/gen@1",
            "allowed_facts": ["facts.verif"],
            "fallback_template_ref": "t/hecho",
        },
        "claims": ["confirmar"],
    }
    assert check(d) == []


def test_allowed_facts_outside_facts_namespace() -> None:
    d = base()
    node(d, "ok_msg")["config"] = {
        "generate": {
            "prompt_ref": "p/gen@1",
            "allowed_facts": ["slots.desc"],
            "fallback_template_ref": "t/hecho",
        },
        "claims": ["confirmar"],
    }
    assert rules(check(d)) == {"G0-10"}


def test_readback_outside_verify() -> None:
    d = base()
    node(d, "buscar")["config"]["args"] = {"q": "readback.status"}
    assert rules(check(d)) == {"G0-10"}


def test_readback_inside_verify_is_valid() -> None:
    assert check(base()) == []


def test_verify_by_fact_outside_facts() -> None:
    d = base()
    node(d, "verificar")["config"]["by"] = "fact:slots.desc"
    assert "G0-10" in rules(check(d))


def test_decisions_in_end_output_map_and_input_view() -> None:
    d = task_base()
    node(d, "fin_ok")["config"]["output_map"] = {"dato": "decisions.d.campo"}
    assert rules(check(d)) == {"G0-10"}
    d2 = _with_decide("modelo@1")
    node(d2, "elige")["config"]["input_view"] = ["decisions.d.campo"]
    assert rules(check(d2)) == {"G0-10"}


def test_g0_10_unresolved_refs_do_not_cascade() -> None:
    d = base()
    node(d, "buscar")["config"]["tool"] = "nada@1"
    node(d, "buscar")["config"]["args"] = {"q": "decisions.d.campo"}
    node(d, "ok_msg")["config"]["template_ref"] = "t/nada"
    assert _direct(g0_10, d) == []


# T-M1-11
def test_decide_on_uncalibrated_field() -> None:
    assert check(_with_decide("modelo@1")) == []
    assert rules(check(_with_decide("modelo_nc@1"))) == {"G0-11"}


def test_g0_11_unresolved_model_is_left_to_g0_02() -> None:
    assert _direct(g0_11, _with_decide("nada@1")) == []


# T-M1-13
def test_claims_with_non_confirm_id() -> None:
    d = base()
    node(d, "ok_msg")["config"]["claims"] = ["confirmar", "buscar"]
    assert rules(check(d)) == {"G0-13"}


def test_claims_with_missing_node_id() -> None:
    d = base()
    node(d, "ok_msg")["config"]["claims"] = ["confirmar", "no_existe"]
    found = _direct(g0_13, d)
    assert [v.rule for v in found] == ["G0-13"]
    assert found[0].path is not None and found[0].path.endswith("/config/claims/1")


# T-M1-14, T-M1-22
def test_undeclarable_outcomes() -> None:
    for outcome in ("abandoned", "escalated"):
        d = base()
        node(d, "fin")["config"]["outcome"] = outcome
        assert rules(check(d)) == {"G0-14"}


def test_mixed_modes() -> None:
    d = base()
    node(d, "fin_cancelado")["config"]["outcome"] = "completed"
    assert rules(check(d)) == {"G0-14"}


def test_mixed_modes_report_the_flow_not_a_node() -> None:
    d = base()
    node(d, "fin_cancelado")["config"]["outcome"] = "completed"
    found = _direct(g0_14, d)
    assert len(found) == 1 and found[0].node_id is None


def test_g0_14_flow_without_end_is_valid() -> None:
    d = base()
    d["nodes"] = [n for n in d["nodes"] if n["type"] != "end"]
    assert _direct(g0_14, d) == []


# T-M1-24
def test_task_flow_failed_end_is_valid() -> None:
    assert check(task_base()) == []


# En un flow mixto end(failed) no es salida segura (M1 §3.7): G0-06 y G0-14 reportan las dos
def test_mixed_flow_failed_end_is_reported_by_g0_06_and_g0_14() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "fin_f"
    d["nodes"].append({"id": "fin_f", "type": "end", "config": {"outcome": "failed"}})
    assert rules(check(d)) == {"G0-06", "G0-14"}


def test_mixed_flow_reports_each_unsafe_end_in_g0_06() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "fin"  # end(resolved) no es una salida segura
    node(d, "buscar")["next"]["timeout"] = "fin_f"
    d["nodes"].append({"id": "fin_f", "type": "end", "config": {"outcome": "failed"}})
    found = [v for v in check(d) if v.rule == "G0-06"]
    assert [v.path for v in found] == ["/nodes/1/next/error", "/nodes/1/next/timeout"]
    assert rules(check(d)) == {"G0-06", "G0-14"}


# T-M1-27
def test_prompt_without_model_profile() -> None:
    d = base()
    node(d, "ok_msg")["config"] = {
        "generate": {
            "prompt_ref": "p/sinperfil@1",
            "allowed_facts": ["facts.verif"],
            "fallback_template_ref": "t/hecho",
        },
        "claims": ["confirmar"],
    }
    assert rules(check(d)) == {"G0-15"}
    node(d, "ok_msg")["config"]["generate"]["prompt_ref"] = "p/gen@1"
    assert check(d) == []


def test_g0_15_unresolved_prompt_is_left_to_g0_02() -> None:
    d = base()
    node(d, "ok_msg")["config"] = {
        "generate": {
            "prompt_ref": "p/nada@1",
            "allowed_facts": ["facts.verif"],
            "fallback_template_ref": "t/hecho",
        },
        "claims": ["confirmar"],
    }
    assert _direct(g0_15, d) == []


# T-M1-28
def test_task_flow_with_waiting_node() -> None:
    d = task_base()
    d["nodes"].insert(
        0,
        {
            "id": "pedir",
            "type": "collect",
            "config": {"slot": "q", "prompt_ref": "t/pedir"},
            "next": {"ok": "buscar", "max_attempts": "fin_fallo"},
        },
    )
    assert rules(check(d)) == {"G0-16"}


def test_conversational_flow_may_wait() -> None:
    assert _direct(g0_16, base()) == []


def test_flow_without_end_has_no_mode_so_g0_16_is_silent() -> None:
    d = task_base()
    d["nodes"] = [n for n in d["nodes"] if n["type"] != "end"]
    d["nodes"].insert(
        0, {"id": "pedir", "type": "collect", "config": {"slot": "q", "prompt_ref": "t/pedir"}, "next": {}}
    )
    assert _direct(g0_16, d) == []


def test_g0_16_waiting_respond_and_confirm_in_task_flow() -> None:
    d = task_base()
    d["nodes"].insert(
        0,
        {
            "id": "resp",
            "type": "respond",
            "config": {"template_ref": "t/pedir", "await": True},
            "next": {"next": "buscar"},
        },
    )
    found = _direct(g0_16, d)
    assert [(v.rule, v.node_id) for v in found] == [("G0-16", "resp")]


# Totalidad y determinismo
def test_all_phase5_rules_are_total_on_a_broken_flow() -> None:
    d = base()
    d["nodes"].append(dict(node(d, "buscar")))  # id duplicado
    node(d, "buscar")["next"] = {"ok": "no_existe"}
    node(d, "buscar")["config"]["tool"] = "nada@1"
    node(d, "ok_msg")["config"]["template_ref"] = "t/nada"
    flow = Flow.model_validate(d)
    ctx = Ctx.build(flow, registry())
    first = [list(rule(ctx)) for rule in PHASE5]
    assert first == [list(rule(ctx)) for rule in PHASE5]


class _CountingRegistry:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls = 0

    def resolve(self, kind: EntityKind, ref: RefSpec) -> RegistryEntity | None:
        self.calls += 1
        result: RegistryEntity | None = self.inner.resolve(kind, ref)
        return result


def test_phase5_scales_linearly_on_a_large_flow() -> None:
    size = 2000
    nodes: list[dict[str, Any]] = []
    for i in range(size):
        nxt = f"n{i + 1}" if i + 1 < size else "fin"
        if i % 2 == 0:
            nodes.append(
                {
                    "id": f"n{i}",
                    "type": "tool",
                    "config": {
                        "tool": "leer@1",
                        "args": {"q": "slots.a", "z": ["facts.h.value.x"]},
                        "save_as": "h",
                    },
                    "next": {"ok": nxt, "error": "esc", "timeout": "esc", "denied": "esc"},
                }
            )
        else:
            nodes.append(
                {
                    "id": f"n{i}",
                    "type": "rule",
                    "config": {
                        "expr": {"and": [{"==": [{"var": "slots.a"}, None]}, {"!": [{"var": "slots.b"}]}]}
                    },
                    "next": {"true": nxt, "false": nxt},
                }
            )
    nodes.append({"id": "fin", "type": "end", "config": {"outcome": "resolved"}})
    nodes.append({"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}})
    counting = _CountingRegistry(registry())
    ctx = Ctx.build(
        Flow.model_validate({"id": "grande", "version": "1.0.0", "priority": 1, "nodes": nodes}), counting
    )  # type: ignore[arg-type]
    found = [v for rule in PHASE5 for v in rule(ctx)]
    assert found == []
    assert counting.calls <= 2 * size  # una consulta al registro por nodo a lo sumo, sin recorridos por nodo
