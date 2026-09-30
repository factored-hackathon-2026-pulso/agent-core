from datetime import timedelta
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_core.domain.nodes import (
    MVP_NODE_KINDS,
    PRODUCTION_NODE_KINDS,
    RESULTS,
    TERMINAL,
    WAITING,
    AwaitApprovalNode,
    CollectNode,
    DecideNode,
    Node,
    RespondNode,
    ToolNode,
    WriteToolNode,
    node_kind,
)

NODE = TypeAdapter(Node)


def _tool(config: dict[str, Any]) -> dict[str, Any]:
    return {"id": "n1", "type": "tool", "config": config, "next": {"ok": "fin"}}


# T-M0-12
def test_tool_with_action_from_is_write_node() -> None:
    node = NODE.validate_python(_tool({"action_from": "confirmar", "save_as": "pqr"}))
    assert isinstance(node, WriteToolNode)
    assert node_kind(node) == "tool_write"


def test_tool_without_action_from_is_read_node() -> None:
    node = NODE.validate_python(_tool({"tool": "buscar@1", "args": {"texto": "slots.x"}, "save_as": "c"}))
    assert isinstance(node, ToolNode)
    assert node_kind(node) == "tool"


def test_write_node_rejects_own_args() -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python(_tool({"action_from": "confirmar", "save_as": "pqr", "args": {"a": 1}}))


def test_respond_exactly_one_of_template_or_generate() -> None:
    generate = {"prompt_ref": "p/x", "fallback_template_ref": "t/y"}
    with pytest.raises(ValidationError):
        NODE.validate_python(
            {"id": "r", "type": "respond", "config": {"template_ref": "t/x", "generate": generate}}
        )
    with pytest.raises(ValidationError):
        NODE.validate_python({"id": "r", "type": "respond", "config": {}})


def test_respond_await_alias_and_claims() -> None:
    node = NODE.validate_python(
        {
            "id": "r",
            "type": "respond",
            "config": {"template_ref": "t/x", "await": True, "claims": ["confirmar"]},
        }
    )
    assert isinstance(node, RespondNode)
    assert node.config.await_ is True
    assert node.config.claims == ["confirmar"]
    assert node.model_dump(by_alias=True)["config"]["await"] is True


def test_decide_has_no_branches() -> None:
    raw = {
        "id": "coincide",
        "type": "decide",
        "config": {"model": "match-cargo@2", "branch_on": "match", "save_as": "coincide"},
        "next": {"unica": "elegir", "low_confidence": "aclarar"},
    }
    assert isinstance(NODE.validate_python(raw), DecideNode)
    raw["config"] = {**raw["config"], "branches": {"unica": "elegir"}}
    with pytest.raises(ValidationError):
        NODE.validate_python(raw)


def test_collect_validator_optional() -> None:
    node = NODE.validate_python(
        {"id": "c", "type": "collect", "config": {"slot": "s", "prompt_ref": "t/p", "max_attempts": 2}}
    )
    assert isinstance(node, CollectNode)
    assert node.config.validator is None


def test_rule_exactly_one_of_policy_or_expr() -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python({"id": "r", "type": "rule", "config": {}})
    with pytest.raises(ValidationError):
        NODE.validate_python({"id": "r", "type": "rule", "config": {"policy": "p@1", "expr": {"==": [1, 1]}}})


def test_rule_expr_falsy_value_counts_as_declared() -> None:
    NODE.validate_python({"id": "r", "type": "rule", "config": {"expr": False}})


def test_unknown_type_rejected() -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python({"id": "k", "type": "knowledge", "config": {}})


@pytest.mark.parametrize("raw", [{"id": "k", "config": {}}, {"id": "k", "type": 3, "config": {}}, "x", None])
def test_missing_or_malformed_type_rejected(raw: Any) -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python(raw)


def test_tool_write_is_not_a_yaml_type() -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python(
            {"id": "w", "type": "tool_write", "config": {"action_from": "c", "save_as": "x"}}
        )


def test_write_config_on_other_type_rejected() -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python({"id": "e", "type": "end", "config": {"action_from": "c", "save_as": "x"}})


def test_extra_field_on_node_rejected() -> None:
    raw = _tool({"tool": "buscar@1", "save_as": "c"})
    with pytest.raises(ValidationError):
        NODE.validate_python({**raw, "evil": 1})


def test_escalate_reason_code_validated() -> None:
    NODE.validate_python({"id": "e", "type": "escalate", "config": {"reason_code": "policy:monto"}})
    with pytest.raises(ValidationError):
        NODE.validate_python({"id": "e", "type": "escalate", "config": {"reason_code": "porque si"}})


@pytest.mark.parametrize("bad", ["Mayus", "1abc", "a-b", "", "ok\n", "ñ"])
def test_save_as_ascii_identifier_only(bad: str) -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python(_tool({"tool": "buscar@1", "save_as": bad}))


def test_verify_by_pattern_is_strict() -> None:
    def verify(by: str) -> dict[str, Any]:
        return {
            "id": "v",
            "type": "verify",
            "config": {"readback": "t@1", "by": by, "predicate": {"==": [1, 1]}, "save_as": "r"},
        }

    NODE.validate_python(verify("idempotency_key"))
    NODE.validate_python(verify("fact:pqr.id"))
    for bad in ("idempotency_key\n", "fact:", "fact:x\ny", "otra"):
        with pytest.raises(ValidationError):
            NODE.validate_python(verify(bad))


@pytest.mark.parametrize("field", ["max_attempts", "step_up_max_attempts", "max_steps"])
@pytest.mark.parametrize("bad", [0, -1])
def test_limits_must_be_positive(field: str, bad: int) -> None:
    raws = {
        "max_attempts": {
            "id": "c",
            "type": "collect",
            "config": {"slot": "s", "prompt_ref": "t/p", "max_attempts": bad},
        },
        "step_up_max_attempts": _tool({"tool": "b@1", "save_as": "c", "step_up_max_attempts": bad}),
        "max_steps": {
            "id": "a",
            "type": "agent",
            "config": {"tools_allowed": [], "max_steps": bad, "prompt_ref": "p/x", "goal": "g",
                       "save_as": "hallazgo", "output_schema": {"type": "object"}},
        },
    }
    with pytest.raises(ValidationError):
        NODE.validate_python(raws[field])


def test_await_approval_timeout_must_be_positive() -> None:
    def raw(timeout: Any) -> dict[str, Any]:
        return {
            "id": "a",
            "type": "await_approval",
            "config": {
                "approver": {"principal_type": "advisor"},
                "summary_template": "t/x",
                "timeout": timeout,
            },
        }

    assert isinstance(NODE.validate_python(raw("PT1H")), AwaitApprovalNode)
    for bad in ("PT0S", "-PT1H", timedelta(0), timedelta(seconds=-5)):
        with pytest.raises(ValidationError):
            NODE.validate_python(raw(bad))


def test_config_is_stored_as_data_not_evaluated() -> None:
    expr = {"==": [{"var": "slots.x"}, "__import__('os')"]}
    node = NODE.validate_python({"id": "r", "type": "rule", "config": {"expr": expr}})
    assert node.config.expr == expr  # type: ignore[union-attr]


def test_results_cover_mvp_kinds() -> None:
    assert frozenset(
        {"decide", "rule", "collect", "tool", "tool_write", "confirm", "verify", "respond", "escalate", "end",
         "agent"}  # `agent` se habilita con el ADR 0019; `subflow` y `await_approval` siguen en producción
    ) == MVP_NODE_KINDS
    assert PRODUCTION_NODE_KINDS == frozenset({"subflow", "await_approval"})
    assert MVP_NODE_KINDS | PRODUCTION_NODE_KINDS == frozenset(RESULTS)
    assert RESULTS["tool_write"] == frozenset({"ok", "denied", "uncertain"})
    assert RESULTS["confirm"] == frozenset({"yes", "no", "unclear", "max_attempts"})
    assert RESULTS["decide"] == frozenset({"low_confidence"})
    assert TERMINAL == frozenset({"escalate", "end"})
    assert WAITING == frozenset({"collect", "confirm"})


def test_results_is_read_only() -> None:
    with pytest.raises(TypeError):
        RESULTS["nuevo"] = frozenset()  # type: ignore[index]


# ADR 0019: el nodo `agent` declara dónde entra su salida y con qué forma
AGENT = {"id": "a", "type": "agent", "next": {"answered": "r", "gave_up": "r"},
         "config": {"tools_allowed": ["leer@1"], "max_steps": 3, "prompt_ref": "p/x@1", "goal": "g",
                    "save_as": "hallazgo", "output_schema": {"type": "object"}}}


def test_agent_config_carries_save_as_and_output_schema() -> None:
    node = NODE.validate_python(AGENT)
    assert node.config.save_as == "hallazgo"  # type: ignore[union-attr]
    assert node.config.output_schema == {"type": "object"}  # type: ignore[union-attr]


@pytest.mark.parametrize("missing", ["save_as", "output_schema"])
def test_agent_config_requires_save_as_and_output_schema(missing: str) -> None:
    config = {k: v for k, v in AGENT["config"].items() if k != missing}  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        NODE.validate_python({**AGENT, "config": config})


def test_agent_save_as_is_an_identifier() -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python({**AGENT, "config": {**AGENT["config"], "save_as": "Hallazgo 1"}})  # type: ignore[dict-item]
