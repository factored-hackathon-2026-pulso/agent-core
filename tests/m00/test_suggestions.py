"""`Suggestion`, `RunResult.suggestions`, nodo `suggest` y evento `suggestions_produced` (ADR 0026, M0)."""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_core.domain import (
    EVENT_EMITTERS,
    METRIC_EVENT_CATALOG,
    RESULTS,
    SCHEMA_VERSION,
    ActionSuggestion,
    AnyEvent,
    EscalateSuggestion,
    Flow,
    ReplySuggestion,
    RunResult,
    SuggestNode,
    ToolSuggestion,
    parse_suggestions,
    suggestion_counts,
)
from tests.m00.samples import make_event

REPLY: dict[str, Any] = {
    "type": "reply",
    "text": "Tu saldo es 10 USD.",
    "citations": ["fact-1"],
    "language": "es",
}
TOOL: dict[str, Any] = {
    "type": "tool",
    "tool": "leer_movimientos@1",
    "args": {"limite": 10},
    "why": "Mirar los últimos movimientos.",
}
ACTION: dict[str, Any] = {
    "type": "action",
    "tool": "radicar_pqr@1",
    "args": {"tipo": "x"},
    "summary": "Radicar una disputa.",
    "executable": False,
}
ESCALATE: dict[str, Any] = {
    "type": "escalate",
    "reason_code": "rule:sugerir-escalamiento",
    "evidence": ["sla_estado: vencido"],
    "motive_draft": "Cliente pide supervisor.",
}
RUN: dict[str, Any] = {
    "run_id": "run-1",
    "release": "rel-1",
    "status": "closed",
    "outcome": "completed",
    "trace_id": "t-1",
}


def test_the_four_types_parse_into_their_classes() -> None:
    parsed = parse_suggestions([REPLY, TOOL, ACTION, ESCALATE])
    assert [type(s) for s in parsed] == [
        ReplySuggestion,
        ToolSuggestion,
        ActionSuggestion,
        EscalateSuggestion,
    ]
    assert suggestion_counts(parsed) == {"count": 4, "reply": 1, "tool": 1, "action": 1, "escalate": 1}


def test_the_empty_list_is_a_valid_result() -> None:
    assert parse_suggestions([]) == []
    assert suggestion_counts([]) == {"count": 0, "reply": 0, "tool": 0, "action": 0, "escalate": 0}


@pytest.mark.parametrize(
    "bad",
    [
        {**REPLY, "type": "other"},  # type outside the closed set
        {k: v for k, v in REPLY.items() if k != "type"},  # type absent
        {**REPLY, "extra": 1},  # extra field
        {**REPLY, "text": "  "},  # empty draft
        {**REPLY, "language": "español"},  # not a locale
        {**REPLY, "citations": ["x"] * 11},  # more than 10 citations
        {**TOOL, "why": ""},
        {**TOOL, "tool": ""},
        {**ACTION, "executable": True},  # an action is never executable at this stage
        {**ACTION, "summary": "x" * 301},
        {**ESCALATE, "reason_code": "fraude"},  # not an M0 code nor rule:/policy:/interrupt:
        {**ESCALATE, "evidence": []},  # an escalation always carries evidence
        {**ESCALATE, "evidence": ["x"] * 6},
        {**ESCALATE, "motive_draft": "x" * 501},
    ],
)
def test_invalid_suggestions_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        parse_suggestions([bad])


def test_an_action_is_not_executable_by_default() -> None:
    action = ActionSuggestion(tool="radicar_pqr@1", args={}, summary="s")
    assert action.executable is False


def test_run_result_carries_suggestions_and_defaults_to_an_empty_list() -> None:
    assert RunResult.model_validate(RUN).suggestions == []  # a result stored before 1.5.0 still loads
    result = RunResult.model_validate({**RUN, "suggestions": [REPLY, ESCALATE]})
    assert [s.type for s in result.suggestions] == ["reply", "escalate"]
    again = RunResult.model_validate_json(result.model_dump_json())
    assert again == result


def test_schema_version_is_minor_bumped() -> None:
    assert SCHEMA_VERSION == "1.5.0"


def test_suggest_node_parses_and_has_its_results() -> None:
    node = {
        "id": "sugerir",
        "type": "suggest",
        "config": {
            "prompt_ref": "p/sugerir@1",
            "goal": "g",
            "reads": ["slots.turnos"],
            "tools_allowed": ["leer_movimientos@1"],
            "escalate": {"reason_code": "rule:sugerir-escalamiento", "evidence_from": ["slots.sla_estado"]},
        },
        "next": {"suggested": "fin", "gave_up": "fin"},
    }
    flow = Flow.model_validate({"id": "f", "version": "1.0.0", "priority": 1, "nodes": [node]})
    assert isinstance(flow.nodes[0], SuggestNode)
    assert flow.nodes[0].config.max_items == 3 and flow.nodes[0].config.actions_allowed == []
    assert RESULTS["suggest"] == frozenset({"suggested", "gave_up"})


@pytest.mark.parametrize(
    "config",
    [
        {"prompt_ref": "p/x@1", "goal": "g", "max_items": 0},
        {
            "prompt_ref": "p/x@1",
            "goal": "g",
            "escalate": {"reason_code": "fraude", "evidence_from": ["slots.a"]},
        },
        {"prompt_ref": "p/x@1", "goal": "g", "escalate": {"reason_code": "rule:x", "evidence_from": []}},
    ],
)
def test_invalid_suggest_configs_are_rejected(config: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Flow.model_validate(
            {
                "id": "f",
                "version": "1.0.0",
                "priority": 1,
                "nodes": [{"id": "s", "type": "suggest", "config": config, "next": {}}],
            }
        )


PAYLOAD = {
    "node_id": "sugerir",
    "result": "ok",
    "count": 2,
    "reply": 1,
    "tool": 0,
    "action": 0,
    "escalate": 1,
    "text_fp": {"alg": "HMAC-SHA256", "kid": "k", "value": "a" * 64},
}


def test_the_event_is_in_the_union_has_an_emitter_and_a_catalog_row() -> None:
    parsed = TypeAdapter(AnyEvent).validate_python(make_event("suggestions_produced", PAYLOAD))
    assert parsed.type == "suggestions_produced"
    assert EVENT_EMITTERS["suggestions_produced"] == frozenset({"M2"})
    assert set(METRIC_EVENT_CATALOG["engine.suggestions_produced"]) >= {
        "count",
        "reply",
        "tool",
        "action",
        "escalate",
    }


def test_the_event_never_carries_text() -> None:
    with pytest.raises(ValidationError):
        bad = make_event("suggestions_produced", {**PAYLOAD, "text": "borrador"})
        TypeAdapter(AnyEvent).validate_python(bad)
