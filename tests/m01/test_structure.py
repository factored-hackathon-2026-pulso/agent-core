from typing import Any

from agent_core.domain import Flow
from agent_core.flows.graph import FlowGraph
from agent_core.flows.validate import validate_flow
from tests.m01.cases import base, check, flow, node, registry, rules


def _rule(nid: str, true: str, false: str, expr: Any = None) -> dict[str, Any]:
    return {"id": nid, "type": "rule", "config": {"expr": expr or {"!": [{"var": "slots.desc"}]}},
            "next": {"true": true, "false": false}}


def test_base_is_valid() -> None:
    assert check(base()) == []


def test_violations_carry_flow_label() -> None:
    d = base()
    del node(d, "buscar")["next"]["timeout"]
    (violation,) = check(d)
    assert (violation.rule, violation.flow, violation.node_id, violation.path) == (
        "G0-03", "base@1.0.0", "buscar", "/nodes/1/next")


# T-M1-02, T-M1-40 (sin cascada)
def test_missing_reference_only_g0_02() -> None:
    d = base()
    node(d, "buscar")["config"]["tool"] = "noexiste@1"
    violations = check(d)
    assert rules(violations) == {"G0-02"}
    assert [v.node_id for v in violations] == ["buscar"]
    assert violations[0].path == "/nodes/1/config/tool"


# T-M1-03
def test_result_without_next() -> None:
    d = base()
    del node(d, "buscar")["next"]["timeout"]
    assert rules(check(d)) == {"G0-03"}


# T-M1-37
def test_next_to_missing_node() -> None:
    d = base()
    node(d, "buscar")["next"]["timeout"] = "fantasma"
    assert rules(check(d)) == {"G0-03"}


def test_unknown_result_key() -> None:
    d = base()
    node(d, "buscar")["next"]["reintento"] = "esc"
    assert rules(check(d)) == {"G0-03"}


def test_terminal_with_next() -> None:
    d = base()
    node(d, "fin")["next"] = {"next": "pedir"}
    assert rules(check(d)) == {"G0-03"}


def test_unreachable_node() -> None:
    d = base()
    d["nodes"].append({"id": "suelto", "type": "end", "config": {"outcome": "resolved"}})
    violations = check(d)
    assert rules(violations) == {"G0-03"} and violations[0].node_id == "suelto"


def _with_decide(d: dict[str, Any], model: str, results: dict[str, str]) -> dict[str, Any]:
    node(d, "buscar")["next"]["ok"] = "elige"
    d["nodes"].append({"id": "elige", "type": "decide",
                       "config": {"model": model, "branch_on": "campo", "save_as": "d"}, "next": results})
    return d


def test_decide_results_are_enum_plus_low_confidence() -> None:
    ok = _with_decide(base(), "modelo@1", {"a": "confirmar", "b": "confirmar", "low_confidence": "esc"})
    assert check(ok) == []
    missing = _with_decide(base(), "modelo@1", {"a": "confirmar", "low_confidence": "esc"})
    assert rules(check(missing)) == {"G0-03"}


def test_decide_enum_with_low_confidence_is_invalid() -> None:
    d = _with_decide(base(), "modelo_lc@1", {"a": "confirmar", "low_confidence": "esc"})
    assert rules(check(d)) == {"G0-03"}


# T-M1-04
def test_cycle_without_waiting_node() -> None:
    d = base()
    node(d, "buscar")["next"]["ok"] = "chk"
    d["nodes"] += [_rule("chk", "chk2", "confirmar"), _rule("chk2", "chk", "confirmar")]
    violations = check(d)
    assert rules(violations) == {"G0-04"}
    assert violations[0].node_id == "chk"


# T-M1-30
def test_scc_with_collect_but_bypassing_loop() -> None:
    d = base()
    node(d, "buscar")["next"]["ok"] = "a"
    d["nodes"] += [
        _rule("a", "b", "confirmar"),
        _rule("b", "a", "c"),
        {"id": "c", "type": "collect", "config": {"slot": "extra", "prompt_ref": "t/pedir"},
         "next": {"ok": "a", "max_attempts": "esc"}},
    ]
    assert rules(check(d)) == {"G0-04"}


def test_self_loop_without_waiting() -> None:
    d = base()
    node(d, "buscar")["next"]["ok"] = "a"
    d["nodes"].append(_rule("a", "a", "confirmar"))
    assert rules(check(d)) == {"G0-04"}


def test_confirm_self_loop_is_fine() -> None:
    assert node(base(), "confirmar")["next"]["unclear"] == "confirmar"
    assert check(base()) == []


# T-M1-07 vía validate_flow: un Flow con nodo de producción construido a mano da solo G0-01
def test_validate_flow_repeats_g0_01() -> None:
    d = base()
    d["nodes"].append({"id": "ag", "type": "agent",
                       "config": {"tools_allowed": ["leer@1"], "max_steps": 3,
                                  "prompt_ref": "p/gen", "goal": "x"}})
    assert rules(check(d)) == {"G0-01"}


def test_reachable_excludes_edges() -> None:
    graph = FlowGraph.build(flow(base()))
    assert "escribir" in graph.from_entry()
    assert "escribir" not in graph.from_entry(frozenset({("confirmar", "yes")}))


# Totalidad: nunca lanza con un Flow que Pydantic aceptó
def test_total_with_duplicate_ids_and_self_loop() -> None:
    d = base()
    d["nodes"].append(_rule("pedir", "pedir", "pedir"))
    violations = check(d)
    assert "G0-01" in rules(violations)
    assert check(d) == violations


def test_total_with_empty_nodes() -> None:
    empty = Flow.model_construct(**{**flow(base()).__dict__, "nodes": []})
    assert rules(validate_flow(empty, registry())) == {"G0-01"}
    assert FlowGraph.build(empty).from_entry() == set()


def test_decide_with_missing_model_only_g0_02() -> None:
    d = _with_decide(base(), "noexiste@1", {"a": "confirmar"})
    assert rules(check(d)) == {"G0-02"}


def test_decide_with_malformed_enum_is_g0_03() -> None:
    d = _with_decide(base(), "modelo@1", {"a": "confirmar"})
    node(d, "elige")["config"]["branch_on"] = "inexistente"
    violations = check(d)
    assert rules(violations) == {"G0-03"}
    assert [v.path for v in violations] == ["/nodes/9/config/branch_on"]


def test_messages_clip_author_strings() -> None:
    d = base()
    long = "x" * 5000
    node(d, "buscar")["next"]["timeout"] = long
    node(d, "buscar")["next"][long] = "esc"
    violations = check(d)
    assert violations and all(len(v.message) < 400 and len(v.path or "") < 400 for v in violations)


def test_json_pointer_escapes_result_keys() -> None:
    d = base()
    node(d, "buscar")["next"]["a/b~c"] = "esc"
    assert "/nodes/1/next/a~1b~0c" in {v.path for v in check(d)}


def test_cycle_message_is_order_independent() -> None:
    d = base()
    node(d, "buscar")["next"]["ok"] = "chk"
    d["nodes"] += [_rule("chk", "chk2", "confirmar"), _rule("chk2", "chk3", "confirmar"),
                   _rule("chk3", "chk", "confirmar")]
    expected = sorted(v.message for v in check(d))
    tail = d["nodes"][1:]
    for permuted in (tail[::-1], tail[1:] + tail[:1], tail[2:] + tail[:2]):
        shuffled = {**d, "nodes": [d["nodes"][0], *permuted]}
        assert sorted(v.message for v in check(shuffled)) == expected


def test_large_flow_is_linear_and_not_recursive() -> None:
    size = 5000
    d = base()
    node(d, "buscar")["next"]["ok"] = "r0"
    d["nodes"] += [_rule(f"r{i}", f"r{i + 1}", f"r{i + 1}") for i in range(size - 1)]
    d["nodes"].append(_rule(f"r{size - 1}", "r0", "confirmar"))
    violations = check(d)  # 5.000 > límite de recursión (1.000): una implementación recursiva fallaría
    assert rules(violations) == {"G0-04"}
    (violation,) = violations
    assert violation.node_id == "r0" and len(violation.message) < 400
