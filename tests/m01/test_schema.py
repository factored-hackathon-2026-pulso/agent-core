from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from agent_core.flows import schema
from agent_core.flows.schema import parse_flow, schema_violations
from agent_core.flows.violations import FlowSchemaError
from tests.m01.cases import base, flow, node


def _rules(d: dict[str, Any], source: str | None = None) -> set[str]:
    with pytest.raises(FlowSchemaError) as info:
        parse_flow(d, source=source)
    return {v.rule for v in info.value.violations}


def test_base_parses() -> None:
    assert parse_flow(base()).id == "base"


# T-M1-01
def test_unknown_type_is_g0_01_with_location() -> None:
    d = base()
    d["nodes"].append({"id": "k", "type": "knowledge", "config": {}})
    with pytest.raises(FlowSchemaError) as info:
        parse_flow(d, source="flows/base@1.0.0.yaml")
    (violation,) = info.value.violations
    assert violation.rule == "G0-01"
    assert violation.flow == "base@1.0.0"
    assert violation.node_id == "k"
    assert violation.path == "flows/base@1.0.0.yaml#/nodes/9"


# T-M1-07 (en el MVP un nodo agent es G0-01)
def test_production_type_rejected() -> None:
    d = base()
    d["nodes"].append(
        {
            "id": "ag",
            "type": "agent",
            "config": {"tools_allowed": ["leer@1"], "max_steps": 3, "prompt_ref": "p/gen", "goal": "x"},
        }
    )
    with pytest.raises(FlowSchemaError) as info:
        parse_flow(d)
    assert [(v.rule, v.message) for v in info.value.violations] == [
        ("G0-01", "tipo de producción no habilitado")
    ]


# T-M1-09
def test_generate_without_fallback_is_g0_09() -> None:
    d = base()
    node(d, "ok_msg")["config"] = {"generate": {"prompt_ref": "p/gen", "allowed_facts": ["facts.verif"]}}
    assert _rules(d) == {"G0-09"}


def _dup(d: dict[str, Any]) -> None:
    d["nodes"].append({"id": "fin", "type": "end", "config": {"outcome": "resolved"}})


def _knowledge(d: dict[str, Any]) -> None:
    node(d, "ok_msg")["config"] = {
        "generate": {"prompt_ref": "p/gen", "fallback_template_ref": "t/hecho", "knowledge_refs": ["faq#x"]}
    }


def _rule_op(d: dict[str, Any]) -> None:
    d["nodes"].append({"id": "r", "type": "rule", "config": {"expr": {"+": [1, 2]}}, "next": {}})


def _predicate_arity(d: dict[str, Any]) -> None:
    node(d, "verificar")["config"]["predicate"] = {"==": [{"var": "readback.status"}]}


def _priority_expr(d: dict[str, Any]) -> None:
    node(d, "esc")["config"]["priority_expr"] = {"merge": [1]}


def _bad_arg(d: dict[str, Any]) -> None:
    node(d, "buscar")["config"]["args"] = {"q": "slots.a.b"}


def _bad_confirm_arg(d: dict[str, Any]) -> None:
    node(d, "confirmar")["config"]["action"]["args"] = {"q": "facts.X.value"}


def _allowed_not_path(d: dict[str, Any]) -> None:
    node(d, "ok_msg")["config"] = {
        "generate": {"prompt_ref": "p/gen", "allowed_facts": ["datos"], "fallback_template_ref": "t/hecho"}
    }


def _output_map_literal(d: dict[str, Any]) -> None:
    node(d, "fin")["config"]["output_map"] = {"x": "USD"}


def _verify_by_fact(d: dict[str, Any]) -> None:
    node(d, "verificar")["config"]["by"] = "fact:slots.a.b"


def _regex(d: dict[str, Any]) -> None:
    node(d, "pedir")["config"]["validator"] = {"kind": "regex", "value": "("}


def _enum(d: dict[str, Any]) -> None:
    node(d, "pedir")["config"]["validator"] = {"kind": "enum", "value": []}


def _type(d: dict[str, Any]) -> None:
    node(d, "pedir")["config"]["validator"] = {"kind": "type", "value": "fecha_rara"}


def _decide_validator(d: dict[str, Any]) -> None:
    node(d, "pedir")["config"]["validator"] = {"kind": "decide", "value": "no valido@@"}


# T-M1-38, T-M1-37 (id duplicado)
@pytest.mark.parametrize(
    "mutate",
    [
        _dup,
        _knowledge,
        _rule_op,
        _predicate_arity,
        _priority_expr,
        _bad_arg,
        _bad_confirm_arg,
        _allowed_not_path,
        _output_map_literal,
        _verify_by_fact,
        _regex,
        _enum,
        _type,
        _decide_validator,
    ],
)
def test_schema_checks_are_g0_01(mutate: Callable[[dict[str, Any]], None]) -> None:
    d = base()
    mutate(d)
    assert _rules(d) == {"G0-01"}
    assert {v.rule for v in schema_violations(flow(d))} == {"G0-01"}


def test_schema_violations_empty_for_base() -> None:
    assert schema_violations(flow(base())) == []


def _hostile_cases() -> list[Any]:
    deep: Any = "x"
    for _ in range(50_000):
        deep = [deep]
    long_id = "n" * 5000
    return [
        None,
        5,
        "flow",
        [],
        [1, 2],
        {},
        {"nodes": []},
        {"id": "a", "version": "1", "nodes": "x"},
        {"id": 1, "version": 2, "nodes": [1, None, "s", []]},
        {
            "id": "a",
            "version": "1.0.0",
            "priority": 1.5,
            "nodes": [{"id": "e", "type": "end", "config": {"outcome": 1.5}}],
        },
        {"id": "a", "version": "1.0.0", "nodes": [{"id": 3, "type": ["x"], "config": 1.5}]},
        {"id": "a", "version": "1.0.0", "nodes": [{"id": long_id, "type": "nope"}]},
        {"id": "a", "version": "1.0.0", "nodes": deep},
        {"id": "a", "version": "1.0.0", "nodes": [{"id": "e", "type": "end", "config": {"outcome": deep}}]},
        deep,
    ]


@pytest.mark.parametrize("raw", _hostile_cases())
def test_parse_flow_is_total_for_hostile_input(raw: Any) -> None:
    with pytest.raises(FlowSchemaError) as info:
        parse_flow(raw, source="f.yaml")
    assert info.value.violations
    for v in info.value.violations:
        assert v.rule in {"G0-01", "G0-09"}
        assert len(v.message) < 400 and len(v.path or "") < 400


def test_messages_do_not_echo_raw_values() -> None:
    d = base()
    d["priority"] = "SECRETO-" + "z" * 300
    with pytest.raises(FlowSchemaError) as info:
        parse_flow(d)
    assert "SECRETO" not in str(info.value)


def test_long_offending_strings_are_truncated() -> None:
    d = base()
    node(d, "buscar")["config"]["args"] = {"q": "slots." + "!" * 1000}
    with pytest.raises(FlowSchemaError) as info:
        parse_flow(d)
    assert all(len(v.message) < 200 for v in info.value.violations)


def test_empty_nodes_is_g0_01() -> None:
    d = base()
    d["nodes"] = []
    assert _rules(d) == {"G0-01"}


@pytest.mark.parametrize("pattern", ["(a+)+", "(.*)*", "(a*)*b", "(a+){2,}", "(?:x+)*"])
def test_catastrophic_regex_is_g0_01(pattern: str) -> None:
    d = base()
    node(d, "pedir")["config"]["validator"] = {"kind": "regex", "value": pattern}
    assert _rules(d) == {"G0-01"}


@pytest.mark.parametrize("pattern", [r"^\d{3}-\d{4}$", "[a-z]+@[a-z]+", "(ab)+", "a+b*"])
def test_ordinary_regex_is_accepted(pattern: str) -> None:
    d = base()
    node(d, "pedir")["config"]["validator"] = {"kind": "regex", "value": pattern}
    assert parse_flow(d).id == "base"


def _paths(d: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    with pytest.raises(FlowSchemaError) as info:
        parse_flow(d)
    return [(v.rule, v.path or "", v.node_id) for v in info.value.violations]


def test_pydantic_paths_are_document_pointers() -> None:
    d = base()
    del node(d, "pedir")["config"]["prompt_ref"]
    assert _paths(d) == [("G0-01", "/nodes/0/config/prompt_ref", "pedir")]
    d = base()
    node(d, "ok_msg")["config"] = {"generate": {"prompt_ref": "p/gen", "allowed_facts": ["facts.verif"]}}
    assert _paths(d) == [("G0-09", "/nodes/5/config/generate/fallback_template_ref", "ok_msg")]
    d = base()
    node(d, "fin")["config"]["nope"] = 1
    assert _paths(d) == [("G0-01", "/nodes/6/config/nope", "fin")]


def test_jsonlogic_messages_are_clipped() -> None:
    d = base()
    node(d, "verificar")["config"]["predicate"] = {"o" * 5000: [1]}
    with pytest.raises(FlowSchemaError) as info:
        parse_flow(d)
    assert info.value.violations
    assert all(len(v.message) < 250 for v in info.value.violations)


def _regex_problems(pattern: str) -> list[str]:
    return schema._regex_safety(pattern)


def test_regex_parser_is_available_in_real_environment() -> None:
    assert schema._PARSER is not None, "re._parser no disponible: la validación de regex fallaría cerrada"
    assert _regex_problems("(a+)+") and _regex_problems("a+") == []


def test_regex_fails_closed_without_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schema, "_PARSER", None)
    assert "no se puede verificar la regex" in _regex_problems("a+")[0]
    d = base()
    node(d, "pedir")["config"]["validator"] = {"kind": "regex", "value": "a+"}
    assert _rules(d) == {"G0-01"}


def test_regex_fails_closed_on_unexpected_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(parse=lambda _p: [(schema._REPEATS[0], "raro")])
    monkeypatch.setattr(schema, "_PARSER", fake)
    (problem,) = _regex_problems("a+")
    assert "no se puede verificar la regex" in problem
    monkeypatch.setattr(schema, "_PARSER", SimpleNamespace(parse=lambda _p: 5))
    assert "no se puede verificar la regex" in _regex_problems("a+")[0]


def _bad_everywhere(size: int) -> dict[str, Any]:
    d = base()
    d["nodes"] = [
        {"id": f"n{i}", "type": "collect", "config": {"slot": "s", "prompt_ref": "t/pedir",
                                                      "validator": {"kind": "regex", "value": "("}},
         "next": {"ok": "n0"}}
        for i in range(size)
    ]
    return d


def test_schema_violations_are_capped_with_a_notice() -> None:
    found = schema_violations(flow(_bad_everywhere(2000)))
    assert len(found) == schema.MAX_ERRORS + 1
    assert all(v.rule == "G0-01" for v in found)
    assert [v.node_id for v in found[:-1]] == [f"n{i}" for i in range(schema.MAX_ERRORS)]
    assert found[-1].message == "se omitieron 1800 errores más"
    assert found[-1].node_id is None
    assert schema_violations(flow(_bad_everywhere(2000))) == found


def test_schema_violations_under_the_cap_have_no_notice() -> None:
    found = schema_violations(flow(_bad_everywhere(schema.MAX_ERRORS)))
    assert len(found) == schema.MAX_ERRORS
    assert not any("omitieron" in v.message for v in found)


def test_parse_flow_raises_with_the_capped_list() -> None:
    with pytest.raises(FlowSchemaError) as info:
        parse_flow(_bad_everywhere(2000), source="flows/malo@1.0.0.yaml")
    violations = info.value.violations
    assert len(violations) == schema.MAX_ERRORS + 1
    assert sum("omitieron" in v.message for v in violations) == 1
    assert all((v.path or "").startswith("flows/malo@1.0.0.yaml#") for v in violations)
