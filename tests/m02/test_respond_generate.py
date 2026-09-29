from decimal import Decimal

from agent_core.domain import EscalationRequest, Message, Prompt, RejectedDraft
from agent_core.interpreter import GenerateResult, Stop
from tests.m02.harness import World, fact, flow, template

GEN = {"id": "g", "type": "respond", "next": {"next": "fin"}, "config": {"generate": {
    "prompt_ref": "p/resumen@1.0.0", "allowed_facts": ["facts.pqr.value.id"],
    "fallback_template_ref": "t/respaldo@1.0.0"}, "claims": []}}
FIN = {"id": "fin", "type": "end", "config": {"outcome": "resolved"}}


def _world() -> World:
    w = World()
    w.add(template("t/respaldo", "Tu disputa {{ facts.pqr.value.id }} quedó radicada."))
    w.add(Prompt.model_validate({
        "id": "p/resumen", "version": "1.0.0", "locales": {"es": "resume", "pt": "resume"},
        "model_profile": "perfil@1.0.0"}))
    return w


def _state(w: World):  # type: ignore[no-untyped-def]
    return w.state(flow(GEN, FIN), facts={"pqr": fact({"id": "pqr-1"})})


def test_generate_delivers_message_and_charges_budgets() -> None:
    w = _world()
    generated = Message(kind="generated", text="Radicamos tu disputa pqr-1.", locale="es")
    w.responder.push(GenerateResult(message=generated, model_calls=2, tokens=120, cost_usd=Decimal("0.004"),
                                    rejected=[RejectedDraft(text_model="borrador", reason="cifra")]))
    out = w.step(_state(w))
    assert out.messages == [generated] and out.stop is Stop.terminal
    assert [d.reason for d in out.rejected_drafts] == ["cifra"]
    used = out.state.budgets_used
    assert (used.turn_model_calls, used.run_tokens, used.run_cost) == (2, 120, Decimal("0.004"))
    (request,) = w.responder.calls
    assert request.node_id == "g" and request.config.fallback_template_ref.id == "t/respaldo"


def test_t_m2_10_degraded_mode_uses_fallback_without_calling_the_responder() -> None:
    w = _world()
    out = w.step(_state(w), degraded=True)
    assert [m.text for m in out.messages] == ["Tu disputa pqr-1 quedó radicada."]
    assert out.messages[0].kind == "template" and w.responder.calls == []
    assert out.state.budgets_used.turn_model_calls == 0


def test_responder_escalation_ends_the_run_with_its_request() -> None:
    w = _world()
    request = EscalationRequest(reason_code="validation_failed", target_queue="general", priority="normal")
    w.responder.push(GenerateResult(escalation=request, model_calls=1, tokens=10))
    out = w.step(_state(w))
    assert out.stop is Stop.terminal and out.escalation == request and out.messages == []
    assert out.state.budgets_used.run_tokens == 10


def test_generate_respects_the_model_call_budget() -> None:
    w = _world()
    from agent_core.interpreter import Resume, advance

    used = {"turn_model_calls": 3, "turn_started_at": w.clock.now()}
    state = _state(w).model_copy(update={"budgets_used": used})
    out = advance(state, w.ctx(), Resume())
    assert out.escalation is not None and out.escalation.reason_code == "budget_exceeded"
    assert w.responder.calls == []


def test_generate_claims_come_from_derive_claims() -> None:
    w = _world()
    w.responder.push(GenerateResult(message=Message(kind="generated", text="ok", locale="es")))
    w.step(_state(w))
    assert w.responder.calls[0].claims == frozenset()  # sin escrituras en este flow, no hay reclamos
