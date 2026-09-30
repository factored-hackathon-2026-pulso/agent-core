"""Adaptador M8 -> `ResponderPort` (m08 §2, m02 D6): el uso del LLM sale de `llm` del evento de la cadena."""

from dataclasses import replace
from decimal import Decimal

from agent_core.composition import ResponderAdapter
from agent_core.domain import EscalationRequest, GatewayError, GatewayErrorKind, Message, RunState
from agent_core.interpreter import GenerateRequest, GenerateResult, ResponderPort
from testing.fakes.gateway import gen
from tests.m08.helpers import CONFIG, GOOD, World

BAD_1 = gen("Tu disputa quedó radicada, avisaremos cuando haya novedades.", ["f-nada"],
            tokens_in=10, tokens_out=5, cost="0.001")
BAD_2 = gen("Tu disputa quedó radicada, avisaremos cuando haya novedades.", ["f-nada"],
            tokens_in=20, tokens_out=8, cost="0.002")


def _adapter(w: World) -> tuple[ResponderPort, list[tuple[GenerateRequest, RunState]]]:
    seen: list[tuple[GenerateRequest, RunState]] = []

    def context_for(request: GenerateRequest, state: RunState):  # type: ignore[no-untyped-def]
        seen.append((request, state))
        return replace(w.ctx, claims=request.claims, node_id=request.node_id)

    return ResponderAdapter(w.responder, context_for), seen


def _request() -> GenerateRequest:
    return GenerateRequest(node_id="responder", config=CONFIG, claims=frozenset({"a", "b"}))


def test_valid_draft_maps_message_events_and_usage() -> None:
    w = World([gen(GOOD, ["f-pqr"], tokens_in=10, tokens_out=5, cost="0.001")])
    adapter, _ = _adapter(w)
    result = adapter.generate(_request(), w.state)
    assert isinstance(result, GenerateResult)
    assert result.message == Message(kind="generated", text=GOOD, locale="es")
    assert result.escalation is None and result.rejected == [] and len(result.events) == 1
    assert (result.model_calls, result.tokens, result.cost_usd) == (1, 15, Decimal("0.001"))


def test_regeneration_accumulates_calls_tokens_and_cost() -> None:
    w = World([BAD_1, gen(GOOD, ["f-pqr"], tokens_in=7, tokens_out=3, cost="0.004")])
    adapter, _ = _adapter(w)
    result = adapter.generate(_request(), w.state)
    assert result.message is not None and len(result.rejected) == 1
    assert (result.model_calls, result.tokens, result.cost_usd) == (2, 25, Decimal("0.005"))


def test_template_fallback_still_charges_the_llm_calls() -> None:
    w = World([BAD_1, BAD_2])
    adapter, _ = _adapter(w)
    result = adapter.generate(_request(), w.state)
    assert result.message is not None and result.message.kind == "template" and len(result.rejected) == 2
    assert (result.model_calls, result.tokens, result.cost_usd) == (2, 43, Decimal("0.003"))


def test_escalation_takes_usage_from_response_failed() -> None:
    w = World([BAD_1, BAD_2], fallback_locales={"pt": "Olá"})
    adapter, _ = _adapter(w)
    result = adapter.generate(_request(), w.state)
    assert result.message is None
    assert result.escalation == EscalationRequest(
        reason_code="validation_failed", target_queue="general", priority="normal")
    assert len(result.rejected) == 2
    assert (result.model_calls, result.tokens, result.cost_usd) == (2, 43, Decimal("0.003"))


def test_gateway_failure_counts_the_call_even_without_a_message() -> None:
    w = World([GatewayError(GatewayErrorKind.unavailable)], fallback_locales={"pt": "Olá"})
    adapter, _ = _adapter(w)
    result = adapter.generate(_request(), w.state)
    assert result.escalation is not None and result.model_calls == 1
    assert result.tokens == 0


def test_degraded_mode_makes_no_model_calls() -> None:
    w = World(degraded=True)
    adapter, _ = _adapter(w)
    result = adapter.generate(_request(), w.state)
    assert result.message is not None and result.message.kind == "template"
    assert (result.model_calls, result.tokens, result.cost_usd) == (0, 0, Decimal("0"))
    assert w.gateway.calls == []


def test_context_factory_receives_the_request_and_the_state() -> None:
    w = World([gen(GOOD, ["f-pqr"])])
    adapter, seen = _adapter(w)
    request = _request()
    adapter.generate(request, w.state)
    assert seen == [(request, w.state)]
