"""Nodo `suggest` (M2 §3.8, ADR 0026): lo que ve el modelo, lo que fija el flow y lo que deja el evento."""

from decimal import Decimal
from typing import Any

import pytest

from agent_core.domain import (
    Budgets,
    EscalateSuggestion,
    IllegalTransition,
    ReplySuggestion,
    ToolSuggestion,
)
from agent_core.interpreter import SuggestResult
from testing.fakes.suggester import ScriptedSuggester
from tests.m02.harness import AGENT, World, flow, slot

REPLY = ReplySuggestion(text="Hola.", citations=[], language="es")
TOOL = ToolSuggestion(tool="leer@1", args={}, why="Mirar.")
TAIL = [
    {"id": "fin", "type": "end", "config": {"outcome": "completed"}},
    {"id": "fallo", "type": "end", "config": {"outcome": "failed"}},
]


def _suggest(**config: Any) -> dict[str, Any]:
    cfg = {
        "prompt_ref": "p/x@1.0.0",
        "goal": "g",
        "reads": ["slots.turnos", "slots.estado"],
        "tools_allowed": ["leer@1.0.0"],
    } | config
    return {"id": "sug", "type": "suggest", "config": cfg, "next": {"suggested": "fin", "gave_up": "fallo"}}


def _ok(*items: Any, **over: Any) -> SuggestResult:
    return SuggestResult(suggestions=list(items), model_calls=1, tokens=30, cost_usd=Decimal("0.002"), **over)


def _world(*results: SuggestResult, **config: Any) -> tuple[World, ScriptedSuggester, Any]:
    w = World()
    port = ScriptedSuggester(results)
    state = w.state(
        flow(_suggest(**config), *TAIL),
        slots={"turnos": slot([{"rol": "cliente", "texto": "hola"}]), "estado": slot("vencido")},
    )
    return w, port, state


def _node(out: Any) -> str:
    return out.state.active_flow.node_id


def test_the_list_goes_to_the_outcome_and_the_flow_continues() -> None:
    w, port, state = _world(_ok(REPLY, TOOL))
    out = w.step(state, suggester=port)
    assert _node(out) == "fin" and out.suggestions == [REPLY, TOOL]


def test_an_empty_list_is_a_valid_result() -> None:
    w, port, state = _world(_ok())
    out = w.step(state, suggester=port)
    assert _node(out) == "fin" and out.suggestions == []


def test_the_model_sees_the_reads_in_the_model_view_with_slots_wrapped() -> None:
    w, port, state = _world(_ok())
    w.step(state, suggester=port)
    (request,) = port.calls
    assert set(request.inputs) == {"slots.turnos", "slots.estado"}
    assert "datos_no_confiables" in str(request.inputs["slots.estado"])


def test_pii_in_a_slot_reaches_the_model_only_as_a_token() -> None:
    w = World()
    port = ScriptedSuggester([_ok()])
    state = w.state(
        flow(_suggest(reads=["slots.turnos"]), *TAIL),
        slots={"turnos": slot("mi correo es ana.prueba@example.test")},
    )
    w.step(state, suggester=port)
    (request,) = port.calls
    shown = str(request.inputs)
    assert "ana.prueba@example.test" not in shown and "⟦" in shown


def test_the_escalation_is_fixed_by_the_flow_from_validated_slots() -> None:
    w, port, state = _world(_ok(), escalate={"reason_code": "rule:x", "evidence_from": ["slots.estado"]})
    w.step(state, suggester=port)
    (request,) = port.calls
    assert request.escalation is not None
    assert request.escalation.reason_code == "rule:x"
    assert request.escalation.evidence == ("estado: vencido",)


def test_a_node_without_escalate_sends_none() -> None:
    w, port, state = _world(_ok())
    w.step(state, suggester=port)
    assert port.calls[0].escalation is None


def test_evidence_that_is_not_a_scalar_fails_closed_without_calling_the_model() -> None:
    w, port, state = _world(_ok(), escalate={"reason_code": "rule:x", "evidence_from": ["slots.turnos"]})
    out = w.step(state, suggester=port)
    assert _node(out) == "fallo" and port.calls == []


def test_evidence_with_pii_fails_closed() -> None:
    w = World()
    port = ScriptedSuggester([_ok()])
    state = w.state(
        flow(
            _suggest(
                reads=["slots.estado"], escalate={"reason_code": "rule:x", "evidence_from": ["slots.estado"]}
            ),
            *TAIL,
        ),
        slots={"estado": slot("ana.prueba@example.test")},
    )
    out = w.step(state, suggester=port)
    assert _node(out) == "fallo" and port.calls == []


def test_a_failure_of_the_suggester_is_gave_up_and_leaves_the_event() -> None:
    w, port, state = _world(
        SuggestResult(failures=["numbers"], model_calls=2, tokens=60, cost_usd=Decimal("0.004"))
    )
    out = w.step(state, suggester=port)
    assert _node(out) == "fallo" and out.suggestions == []
    (event,) = [e for e in out.events if e.type == "suggestions_produced"]
    assert event.payload.result == "failed" and event.payload.failures == ["numbers"]
    assert event.payload.count == 0


def test_the_event_carries_counts_and_a_fingerprint_never_text() -> None:
    esc = EscalateSuggestion(
        reason_code="rule:x", evidence=["estado: vencido"], motive_draft="Pide supervisor."
    )
    w, port, state = _world(_ok(esc, REPLY, TOOL, regenerations=1))
    out = w.step(state, suggester=port)
    (event,) = [e for e in out.events if e.type == "suggestions_produced"]
    p = event.payload
    assert (p.node_id, p.result, p.count, p.reply, p.tool, p.action, p.escalate, p.regenerations) == (
        "sug",
        "ok",
        3,
        1,
        1,
        0,
        1,
        1,
    )
    assert p.text_fp is not None
    dumped = event.model_dump_json()
    assert "Pide supervisor" not in dumped and "Hola." not in dumped


def test_the_usage_is_charged_to_the_budget() -> None:
    w, port, state = _world(_ok())
    out = w.step(state, suggester=port)
    assert out.state.budgets_used.turn_model_calls == 1
    assert out.state.budgets_used.run_tokens == 30


def test_optional_reads_are_sent_only_when_they_exist() -> None:
    w = World()
    port = ScriptedSuggester([_ok(), _ok()])
    both = {
        "turnos": slot([{"rol": "cliente", "texto": "hola"}]),
        "estado": slot("vencido"),
        "motivo": slot("traspaso"),
    }
    f = flow(
        _suggest(reads=["slots.turnos"], optional_reads=["slots.motivo", "slots.estado", "slots.nada"]), *TAIL
    )
    w.step(w.state(f, slots=both), suggester=port)
    w.step(w.state(f, slots={"turnos": both["turnos"]}), suggester=port)
    assert set(port.calls[0].inputs) == {"slots.turnos", "slots.motivo", "slots.estado"}
    assert set(port.calls[1].inputs) == {"slots.turnos"}


def test_a_degraded_turn_does_not_call_the_model() -> None:
    w, port, state = _world(_ok(REPLY))
    out = w.step(state, suggester=port, degraded=True)
    assert _node(out) == "fallo" and port.calls == []


def test_a_missing_read_path_does_not_call_the_model() -> None:
    w = World()
    port = ScriptedSuggester([_ok()])
    state = w.state(flow(_suggest(), *TAIL), slots={"turnos": slot([])})  # slots.estado is missing
    out = w.step(state, suggester=port)
    assert _node(out) == "fallo" and port.calls == []


def test_an_exhausted_model_budget_escalates_like_the_other_model_nodes() -> None:
    budgets = Budgets.model_validate({**AGENT.budgets.model_dump(), "max_model_calls_per_turn": 1})
    w = World(agent=AGENT.model_copy(update={"budgets": budgets}))
    port = ScriptedSuggester([_ok()])
    first = _suggest()
    first["next"]["suggested"] = "sug2"
    second = {**_suggest(), "id": "sug2"}
    state = w.state(
        flow(first, second, *TAIL),
        slots={"turnos": slot([{"rol": "cliente", "texto": "hola"}]), "estado": slot("vencido")},
    )
    out = w.step(state, suggester=port)
    assert out.escalation is not None and out.escalation.reason_code == "budget_exceeded"
    assert len(port.calls) == 1


def test_without_a_suggester_port_it_is_a_wiring_error() -> None:
    w, _, state = _world(_ok())
    with pytest.raises(IllegalTransition):
        w.step(state)
