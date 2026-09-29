from dataclasses import replace
from decimal import Decimal

from agent_core.domain import EscalationRequest, GatewayError, GatewayErrorKind, Message, ResponseEmitted
from agent_core.ports import GenerationResult
from testing.fakes.gateway import gen
from tests.m08.helpers import CONFIG, GOOD, World

BAD_CITATION = gen("Tu disputa quedó radicada, avisaremos cuando haya novedades.", ["f-nada"],
                   tokens_in=10, tokens_out=5, cost="0.001")
BAD_CITATION_2 = gen("Tu disputa quedó radicada, avisaremos cuando haya novedades.", ["f-nada"],
                     tokens_in=20, tokens_out=8, cost="0.002")


def _payload(events):  # type: ignore[no-untyped-def]
    (event,) = events
    assert isinstance(event, ResponseEmitted)
    return event.payload


def test_first_valid_draft_is_generated_without_regeneration() -> None:
    w = World([gen(GOOD, ["f-pqr"])])
    message, rejected, events = w.run()
    assert message == Message(kind="generated", text=GOOD, locale="es") and rejected == []
    payload = _payload(events)
    assert (payload.kind, payload.fallback_used, payload.validator.ok, payload.validator.regenerations) == (
        "generated", False, True, 0)
    assert payload.llm is not None and payload.llm.calls == 1 and payload.claims == ["a", "b"]
    assert payload.node_id == "responder" and payload.transcript_fp is None


def test_gateway_receives_only_model_view_of_allowed_facts_and_the_schema() -> None:
    w = World([gen(GOOD, ["f-pqr"])])
    w.run()
    (call,) = w.gateway.calls
    assert call.inputs["facts"] == {"pqr": {"fact_id": "f-pqr", "value": w.fact.value}}
    assert call.schema is not None and call.schema["required"] == ["text", "citations"]
    assert call.locale == "es" and str(call.prompt) == "resumen@1.0.0"


def test_first_invalid_second_valid_regenerates_once() -> None:
    w = World([BAD_CITATION, gen(GOOD, ["f-pqr"])])
    message, rejected, events = w.run()
    assert isinstance(message, Message) and message.kind == "generated"
    assert len(rejected) == 1 and _payload(events).validator.regenerations == 1
    assert _payload(events).llm.calls == 2  # type: ignore[union-attr]


def test_t_m8_07a_two_rejections_fall_back_to_the_template() -> None:
    w = World([BAD_CITATION, BAD_CITATION_2])
    message, rejected, events = w.run()
    assert message == Message(kind="template", text="Tu disputa pqr-1 quedó radicada.", locale="es")
    payload = _payload(events)
    assert payload.fallback_used and payload.kind == "template" and payload.validator.ok is False
    assert payload.validator.regenerations == 1 and payload.validator.failures == ["citations"]
    assert len(w.gateway.calls) == 2
    feedback = w.gateway.calls[1].inputs["validation_feedback"]
    assert isinstance(feedback, list) and feedback[0]["check"] == "citations"  # type: ignore[call-overload]
    assert "validation_feedback" not in w.gateway.calls[0].inputs
    assert len(rejected) == 2


def test_t_m8_07b_two_rejections_and_impossible_template_escalate() -> None:
    w = World([BAD_CITATION, BAD_CITATION_2], fallback_locales={"pt": "Olá"})
    message, rejected, events = w.run()
    assert message == EscalationRequest(reason_code="validation_failed", target_queue="general",
                                        priority="normal")
    assert len(rejected) == 2 and events == []


def test_missing_fact_in_template_escalates() -> None:
    w = World([BAD_CITATION, BAD_CITATION_2], fallback="{{ facts.pqr.value.nada }}")
    assert isinstance(w.run()[0], EscalationRequest)


def test_t_m8_08_degraded_mode_does_not_call_the_gateway() -> None:
    w = World(degraded=True)
    message, rejected, events = w.run()
    assert w.gateway.calls == [] and isinstance(message, Message) and message.kind == "template"
    payload = _payload(events)
    assert payload.llm is None and payload.kind == "template" and payload.fallback_used is True
    assert rejected == []


def test_t_m8_09_rejected_drafts_carry_reason_and_failures() -> None:
    w = World([BAD_CITATION, BAD_CITATION_2])
    _, rejected, _ = w.run()
    assert [d.failures for d in rejected] == [["citations"], ["citations"]]
    assert all("citations" in d.reason and "cita_inexistente" in d.reason for d in rejected)
    assert rejected[0].text_model == "Tu disputa quedó radicada, avisaremos cuando haya novedades."


def test_t_m8_11a_usage_is_summed_across_generation_and_regeneration() -> None:
    w = World([BAD_CITATION, BAD_CITATION_2], ms=150)
    _, _, events = w.run()
    llm = _payload(events).llm
    assert llm is not None
    assert (llm.calls, llm.tokens_in, llm.tokens_out, llm.cost_usd) == (2, 30, 13, Decimal("0.003"))
    assert llm.latency_ms == 300 and llm.models == ["scripted-1"] and llm.cost_known is True


def test_t_m8_11b_degraded_payload_has_no_llm() -> None:
    _, _, events = World(degraded=True).run()
    assert _payload(events).llm is None


def test_gateway_error_goes_straight_to_the_template_without_regeneration() -> None:
    w = World([GatewayError(GatewayErrorKind.unavailable)])
    message, rejected, events = w.run()
    assert isinstance(message, Message) and message.kind == "template" and rejected == []
    payload = _payload(events)
    assert payload.llm is not None and payload.llm.calls == 1 and payload.llm.cost_known is False
    assert len(w.gateway.calls) == 1 and payload.fallback_used


def test_unparseable_output_is_a_format_rejection_that_consumes_the_regeneration() -> None:
    bad = GenerationResult(output={"foo": 1}, tokens_in=1, tokens_out=1, cost_usd=Decimal("0.001"),
                           model="scripted-1")
    w = World([bad, gen(GOOD, ["f-pqr"])])
    message, rejected, events = w.run()
    assert isinstance(message, Message) and message.kind == "generated"
    assert rejected[0].failures == ["format"] and rejected[0].text_model == ""
    assert _payload(events).validator.regenerations == 1


def test_template_with_clear_pii_escalates() -> None:
    w = World([BAD_CITATION, BAD_CITATION_2], find_clear_pii=lambda text: ["cliente.document_number"])
    message, _, _ = w.run()
    assert isinstance(message, EscalationRequest) and message.reason_code == "validation_failed"


def test_regeneration_count_is_a_parameter() -> None:
    w = World([BAD_CITATION, BAD_CITATION_2, gen(GOOD, ["f-pqr"])])
    ctx = replace(w.ctx, max_regenerations=2)
    message, rejected, events = w.responder.generate(CONFIG, w.state, ctx)
    assert isinstance(message, Message) and message.kind == "generated" and len(rejected) == 2
    assert _payload(events).validator.regenerations == 2


def test_determinism_same_ports_same_events() -> None:
    first = World([BAD_CITATION, BAD_CITATION_2]).run()
    second = World([BAD_CITATION, BAD_CITATION_2]).run()
    assert first == second


def test_undeclared_fact_names_are_not_allowed_or_sent() -> None:
    w = World([gen(GOOD, ["f-pqr"]), gen(GOOD, ["f-pqr"])])
    config = CONFIG.model_copy(update={"allowed_facts": []})
    message, rejected, _ = w.responder.generate(config, w.state, w.ctx)
    assert isinstance(message, Message) and message.kind == "template"
    assert [d.failures for d in rejected] == [["citations"], ["citations"]]
    assert all("cita_no_permitida" in d.reason for d in rejected)
    assert w.gateway.calls[0].inputs["facts"] == {}
