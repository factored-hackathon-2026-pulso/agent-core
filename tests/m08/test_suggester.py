"""`Suggester` de M8 (ADR 0026): generar → validar → regenerar una vez; el modelo no crea el escalamiento."""

from dataclasses import replace
from decimal import Decimal
from typing import Any

from agent_core.domain import (
    EntityRef,
    EscalateSuggestion,
    GatewayError,
    GatewayErrorKind,
    ReplySuggestion,
    SuggestEscalation,
    ToolSuggestion,
)
from agent_core.ports import GenerationResult
from agent_core.response import SUGGESTIONS_SCHEMA, Suggester, SuggesterContext, ToolEntry
from testing.fakes.clock import FakeClock
from testing.fakes.gateway import ScriptedGateway
from tests.m08.helpers import make_ctx, make_fact

MOVEMENTS = ToolEntry(
    ref="leer_movimientos@1",
    exact="leer_movimientos@1.0.0",
    description="Movimientos.",
    args_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {"limite": {"type": "integer"}},
    },
)
WRITE = ToolEntry(
    ref="radicar_pqr@1",
    exact="radicar_pqr@1.0.0",
    description="Radicar.",
    args_schema={"type": "object", "properties": {"tipo": {"type": "string"}}},
)
ESCALATION = SuggestEscalation(reason_code="rule:sugerir", evidence=("sla_estado: vencido",))
PRODUCTS = make_fact("fact-productos", {"saldo": Decimal("1342.80"), "moneda": "USD"})


def out(*items: dict[str, Any]) -> GenerationResult:
    return GenerationResult(
        output={"suggestions": list(items)},
        tokens_in=40,
        tokens_out=20,
        cost_usd=Decimal("0.002"),
        model="scripted-1",
    )


REPLY = {
    "type": "reply",
    "text": "El saldo es de 1342.80 USD, según tus productos.",
    "citations": ["fact-productos"],
    "language": "es",
}
TOOL = {"type": "tool", "tool": "leer_movimientos@1", "args": {"limite": 10}, "why": "Ver los movimientos."}
ESC = {"type": "escalate", "motive_draft": "El cliente pide supervisor."}


def world(
    *script: Any,
    escalation: SuggestEscalation | None = None,
    actions: tuple[ToolEntry, ...] = (),
    max_items: int = 3,
    **validation: Any,
) -> tuple[Suggester, SuggesterContext, ScriptedGateway]:
    gateway = ScriptedGateway(list(script))
    ctx = SuggesterContext(
        gateway=gateway,
        clock=FakeClock(),
        prompt=EntityRef(id="sugerir", version="1.0.0"),
        locale="es",
        goal="g",
        inputs={"slots.turnos": "x"},
        citable={"productos": "fact-productos"},
        tools=(MOVEMENTS,),
        actions=actions,
        escalation=escalation,
        max_items=max_items,
        validation=make_ctx({"fact-productos": PRODUCTS}, {"fact-productos"}, **validation),
    )
    return Suggester(), ctx, gateway


def test_the_four_kinds_come_out_typed_and_ordered_escalation_reply_tool_action() -> None:
    action = {"type": "action", "tool": "radicar_pqr@1", "args": {"tipo": "x"}, "summary": "Radicar."}
    s, ctx, _ = world(out(TOOL, action, REPLY, ESC), escalation=ESCALATION, actions=(WRITE,), max_items=4)
    outcome = s.generate(ctx)
    assert outcome.failures == []
    assert [x.type for x in outcome.suggestions] == ["escalate", "reply", "tool", "action"]
    esc = outcome.suggestions[0]
    assert isinstance(esc, EscalateSuggestion)
    # reason and evidence are the flow's; only the motive is the model's
    assert (esc.reason_code, esc.evidence, esc.motive_draft) == (
        "rule:sugerir",
        ["sla_estado: vencido"],
        "El cliente pide supervisor.",
    )
    assert (
        isinstance(outcome.suggestions[2], ToolSuggestion)
        and outcome.suggestions[2].tool == "leer_movimientos@1"
    )


def test_an_empty_list_is_valid() -> None:
    s, ctx, _ = world(out())
    outcome = s.generate(ctx)
    assert outcome.failures == [] and outcome.suggestions == []


def test_the_model_cannot_create_an_escalation_the_flow_did_not_declare() -> None:
    s, ctx, gateway = world(out(ESC), out(ESC))  # the second try repeats it
    outcome = s.generate(ctx)
    assert outcome.suggestions == [] and "escalate_not_allowed" in outcome.failures
    assert len(gateway.calls) == 2  # one regeneration


def test_a_declared_escalation_must_be_present_exactly_once() -> None:
    s, ctx, _ = world(out(REPLY), out(REPLY), escalation=ESCALATION)
    assert "escalate_missing" in s.generate(ctx).failures
    s, ctx, _ = world(out(ESC, ESC), out(ESC, ESC), escalation=ESCALATION)
    assert "duplicate" in s.generate(ctx).failures


def test_the_model_cannot_override_the_reason_or_the_evidence() -> None:
    forged = {**ESC, "reason_code": "policy:inventado", "evidence": ["otra cosa"]}
    s, ctx, _ = world(out(forged), out(forged), escalation=ESCALATION)
    assert "format" in s.generate(ctx).failures  # extra properties are not in the model's schema


def test_a_second_reply_is_rejected_not_dropped_silently() -> None:
    s, ctx, _ = world(out(REPLY, REPLY), out(REPLY, REPLY))
    assert "duplicate" in s.generate(ctx).failures


def test_more_than_max_items_is_rejected() -> None:
    s, ctx, _ = world(out(TOOL, TOOL), out(TOOL, TOOL), max_items=1)
    assert "too_many" in s.generate(ctx).failures


def test_a_tool_outside_the_catalog_or_with_bad_args_is_rejected() -> None:
    s, ctx, _ = world(out({**TOOL, "tool": "borrar_todo@1"}), out({**TOOL, "tool": "borrar_todo@1"}))
    assert "tool_not_allowed" in s.generate(ctx).failures
    bad = {**TOOL, "args": {"limite": "diez"}}
    s, ctx, _ = world(out(bad), out(bad))
    assert "args_invalid" in s.generate(ctx).failures
    extra = {**TOOL, "args": {"limite": 1, "customer_id": "cust-001"}}  # the subject is never an argument
    s, ctx, _ = world(out(extra), out(extra))
    assert "args_invalid" in s.generate(ctx).failures


def test_the_exact_ref_of_a_tool_is_accepted_and_normalized_to_the_authored_one() -> None:
    s, ctx, _ = world(out({**TOOL, "tool": "leer_movimientos@1.0.0"}))
    (tool,) = s.generate(ctx).suggestions
    assert isinstance(tool, ToolSuggestion) and tool.tool == "leer_movimientos@1"


def test_an_action_is_rejected_when_the_node_allows_none() -> None:
    action = {"type": "action", "tool": "radicar_pqr@1", "args": {}, "summary": "Radicar."}
    s, ctx, _ = world(out(action), out(action))
    assert "action_not_allowed" in s.generate(ctx).failures


def test_a_figure_without_a_cited_source_is_rejected_and_regenerated() -> None:
    bad = {**REPLY, "text": "El saldo es de 999.99 USD.", "citations": ["fact-productos"]}
    s, ctx, gateway = world(out(bad), out(REPLY))
    outcome = s.generate(ctx)
    assert outcome.failures == [] and outcome.regenerations == 1
    feedback = gateway.calls[1].inputs["validation_feedback"]
    assert feedback and "999" not in str(feedback)  # the feedback names the check, never the text


def test_a_citation_to_a_fact_not_listed_is_rejected() -> None:
    s, ctx, _ = world(
        out({**REPLY, "citations": ["fact-inventado"]}), out({**REPLY, "citations": ["fact-inventado"]})
    )
    assert "citations" in s.generate(ctx).failures


def test_the_reply_language_must_be_the_runs() -> None:
    s, ctx, _ = world(out({**REPLY, "language": "pt"}), out({**REPLY, "language": "pt"}))
    assert "language" in s.generate(ctx).failures


def test_a_token_in_the_output_is_rejected() -> None:
    leaked = {**REPLY, "text": "Hola ⟦pii:1⟧, tu saldo es de 1342.80 USD."}
    s, ctx, _ = world(out(leaked), out(leaked))
    assert "tokens_pii" in s.generate(ctx).failures


def test_clear_pii_in_any_text_is_rejected_even_in_why_or_motive() -> None:
    def pii(text: str) -> list[str]:
        return ["pattern:email"] if "@" in text else []

    leaky_tool = {**TOOL, "why": "Escríbele a ana@example.test"}
    s, ctx, _ = world(out(leaky_tool), out(leaky_tool), find_clear_pii=pii)
    assert "tokens_pii" in s.generate(ctx).failures
    leaky_esc = {**ESC, "motive_draft": "Contactar a ana@example.test"}
    s, ctx, _ = world(out(leaky_esc), out(leaky_esc), escalation=ESCALATION, find_clear_pii=pii)
    assert "tokens_pii" in s.generate(ctx).failures


def test_a_figure_in_why_or_motive_must_come_from_a_fact_of_the_node() -> None:
    s, ctx, _ = world(
        out({**TOOL, "why": "Revisar el cargo de 77.10 USD."}),
        out({**TOOL, "why": "Revisar el cargo de 77.10 USD."}),
    )
    assert "numbers" in s.generate(ctx).failures
    ok = {**TOOL, "why": "El saldo de 1342.80 USD ya está en los productos."}
    s, ctx, _ = world(out(ok))
    assert s.generate(ctx).failures == []


def test_a_schema_violation_is_a_format_failure_without_echoing_the_value() -> None:
    s, ctx, gateway = world(out({"type": "reply", "secreto": "4111111111111111"}), out())
    outcome = s.generate(ctx)
    assert outcome.failures == [] and outcome.regenerations == 1  # the second answer (empty list) is valid
    assert "4111" not in str(gateway.calls[1].inputs["validation_feedback"])


def test_an_invalid_output_error_regenerates_and_other_gateway_errors_stop() -> None:
    s, ctx, gateway = world(GatewayError(GatewayErrorKind.invalid_output), out(TOOL))
    outcome = s.generate(ctx)
    assert outcome.failures == [] and len(gateway.calls) == 2
    s, ctx, gateway = world(GatewayError(GatewayErrorKind.timeout))
    outcome = s.generate(ctx)
    assert outcome.suggestions == [] and outcome.failures == ["gateway_timeout"] and len(gateway.calls) == 1


def test_the_model_sees_the_catalog_the_citable_facts_and_the_escalation_reason_but_not_its_evidence() -> (
    None
):
    s, ctx, gateway = world(out(), escalation=ESCALATION)
    s.generate(ctx)
    call = gateway.calls[0]
    assert call.schema == SUGGESTIONS_SCHEMA and call.locale == "es"
    sent = call.inputs
    assert sent["citable_facts"] == {"productos": "fact-productos"}
    assert sent["tools"] == [
        {"tool": "leer_movimientos@1", "description": "Movimientos.", "args_schema": MOVEMENTS.args_schema}
    ]
    # the evidence is the flow's and goes to the output, never to the model (it sees the facts in `reads`)
    assert sent["escalation"] == {"required": True, "reason_code": "rule:sugerir"}
    assert "vencido" not in str(sent)
    assert sent["max_items"] == 3


def test_the_usage_of_every_attempt_is_reported() -> None:
    s, ctx, _ = world(out({**REPLY, "language": "pt"}), out(REPLY))
    outcome = s.generate(ctx)
    assert outcome.llm is not None and outcome.llm.calls == 2 and outcome.llm.tokens_in == 80


def test_degraded_does_not_call_the_model() -> None:
    s, ctx, gateway = world(out(REPLY))
    outcome = s.generate(replace(ctx, degraded=True))
    assert outcome.failures == ["degraded"] and gateway.calls == []


def test_a_reply_keeps_its_citations() -> None:
    s, ctx, _ = world(out(REPLY))
    (reply,) = s.generate(ctx).suggestions
    assert isinstance(reply, ReplySuggestion) and reply.citations == ["fact-productos"]


def test_pii_in_the_args_of_a_tool_is_rejected() -> None:
    def pii(text: str) -> list[str]:
        return ["pattern:email"] if "@" in text else []

    schema_free = ToolEntry(ref="buscar@1", exact="buscar@1.0.0", description="Busca.",
                            args_schema={"type": "object", "properties": {"q": {"type": "string"}}})
    leaky = {"type": "tool", "tool": "buscar@1", "args": {"q": "ana@example.test"}, "why": "Buscar."}
    s, ctx, _ = world(out(leaky), out(leaky), find_clear_pii=pii)
    ctx = replace(ctx, tools=(schema_free,))
    assert "tokens_pii" in s.generate(ctx).failures
