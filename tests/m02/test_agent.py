"""Nodo `agent` (M2 §3.7, ADR 0019): bucle acotado de lectura y cálculo; su salida entra como hecho."""

from decimal import Decimal
from typing import Any

import pytest

from agent_core.domain import EntityRef, GatewayError, GatewayErrorKind, IllegalTransition
from agent_core.interpreter import AgentFinal, AgentStepResult, AgentToolCall, Resume, Stop
from agent_core.ports import ToolStatus
from testing.fakes.agent import ScriptedAgent
from testing.fakes.tools import Scripted
from tests.m02.harness import World, fact, flow, slot, tool_def

SCHEMA = {"type": "object", "properties": {"resumen": {"type": "string"}}, "required": ["resumen"],
          "additionalProperties": False}
BUSCAR = EntityRef(id="buscar", version="1.0.0")
ESCRIBIR = EntityRef(id="escribir", version="1.0.0")
TAIL = [
    {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}},
]


def _agent(**config: Any) -> dict[str, Any]:
    cfg = {"tools_allowed": ["buscar@1.0.0"], "max_steps": 3, "prompt_ref": "p/x@1.0.0", "goal": "g",
           "save_as": "hallazgo", "output_schema": SCHEMA} | config
    return {"id": "a", "type": "agent", "config": cfg, "next": {"answered": "fin", "gave_up": "esc"}}


def _call(tool: EntityRef = BUSCAR, **args: Any) -> AgentStepResult:
    return AgentStepResult(AgentToolCall(tool=tool, args=args), tokens=10, cost_usd=Decimal("0.001"))


def _final(output: Any = None) -> AgentStepResult:
    return AgentStepResult(AgentFinal(output={"resumen": "ok"} if output is None else output), tokens=20,
                           cost_usd=Decimal("0.002"))


def _world(*steps: AgentStepResult, **config: Any) -> tuple[World, ScriptedAgent, Any]:
    w = World()
    w.add_tool(tool_def("buscar", source="tx"), handler=lambda a: {"echo": a.get("q"), "n": 2})
    w.add_tool(tool_def("escribir", "write_reversible"))
    port = ScriptedAgent(steps)
    return w, port, w.state(flow(_agent(**config), *TAIL))


def _types(out: Any, kind: str) -> list[Any]:
    return [e for e in out.events if e.type == kind]


def _node(out: Any) -> str:
    return out.state.active_flow.node_id


def test_answered_stores_the_output_as_a_fact_with_provenance() -> None:
    w, port, state = _world(_call(q="cargo"), _final())
    out = w.step(state, agents=port)
    assert _node(out) == "fin"
    stored = out.state.facts["hallazgo"]
    assert stored.value == {"resumen": "ok"}
    assert stored.source.kind == "agent"
    (tool_event,) = _types(out, "tool_called")
    assert stored.source.inputs == [tool_event.payload.call_id]  # procedencia: las llamadas del bucle
    assert w.tools.calls[0].args == {"q": "cargo"}


def test_each_step_emits_agent_step_and_tool_calls_keep_their_own_event() -> None:
    w, port, state = _world(_call(q="x"), _final())
    out = w.step(state, agents=port)
    steps = _types(out, "agent_step")
    assert [(s.payload.step, s.payload.kind) for s in steps] == [(1, "tool"), (2, "final")]
    assert steps[0].payload.tool == BUSCAR and steps[0].payload.status is ToolStatus.ok
    assert steps[0].payload.call_id == _types(out, "tool_called")[0].payload.call_id
    assert steps[1].payload.text_fp is not None and steps[1].payload.tool is None
    assert "ok" not in steps[1].model_dump_json().replace('"ok"', "")  # el texto final no va en el evento


def test_model_calls_tokens_and_cost_are_charged_per_step() -> None:
    w, port, state = _world(_call(), _final())
    used = w.step(state, agents=port).state.budgets_used
    assert (used.turn_model_calls, used.run_tokens, used.run_cost) == (2, 30, Decimal("0.003"))


def test_the_model_sees_tool_results_in_model_view_and_can_reuse_tokens() -> None:
    w = World()
    w.add_tool(tool_def("buscar", source="customers"), handler=lambda a: {"document_number": "12345678"})
    w.add_tool(tool_def("otra", source="customers"), handler=lambda a: {"ok": True})
    port = ScriptedAgent([_call(), _call(EntityRef(id="otra", version="1.0.0"), doc="⟦doc:1⟧"), _final()])
    f = flow(_agent(tools_allowed=["buscar@1.0.0", "otra@1.0.0"]), *TAIL)
    w.step(w.state(f), agents=port)
    seen = port.calls[1].observations[0]
    assert seen.result == {"document_number": "⟦doc:1⟧"}  # el modelo nunca ve el valor real
    assert "12345678" not in repr(port.calls)
    assert w.tools.calls[1].args == {"doc": "12345678"}  # la tool recibe el valor que el token protege


def test_a_tool_outside_tools_allowed_is_not_executed() -> None:
    w, port, state = _world(_call(EntityRef(id="otra", version="1.0.0")), _final())
    out = w.step(state, agents=port)
    assert w.tools.calls == []
    assert _types(out, "access_denied") and _node(out) == "fin"
    assert port.calls[1].observations[0].status is ToolStatus.denied
    assert _types(out, "agent_step")[0].payload.status is ToolStatus.denied


def test_a_write_tool_is_never_executed_even_if_listed() -> None:
    both = ["buscar@1.0.0", "escribir@1.0.0"]
    w, port, state = _world(_call(ESCRIBIR, q="x"), _final(), tools_allowed=both)
    out = w.step(state, agents=port)
    assert w.tools.calls == [] and port.calls[1].observations[0].status is ToolStatus.denied
    assert _node(out) == "fin"


@pytest.mark.parametrize("status", [ToolStatus.error, ToolStatus.timeout, ToolStatus.step_up_required])
def test_a_failing_tool_goes_back_to_the_model_as_a_result(status: ToolStatus) -> None:
    w = World()
    w.add_tool(tool_def("buscar"), script=[Scripted(status)])
    port = ScriptedAgent([_call(), _final()])
    out = w.step(w.state(flow(_agent(), *TAIL)), agents=port)
    assert _node(out) == "fin"
    expected = ToolStatus.denied if status is ToolStatus.step_up_required else status
    assert port.calls[1].observations[0].status is expected


def test_max_steps_exhausted_gives_up() -> None:
    w, port, state = _world(_call(), _call(), _call(), max_steps=3)
    out = w.step(state, agents=port)
    assert _node(out) == "esc" and "hallazgo" not in out.state.facts
    assert len(port.calls) == 3


def test_invalid_output_gets_one_regeneration_with_feedback() -> None:
    w, port, state = _world(_final({"resumen": 5}), _final())
    out = w.step(state, agents=port)
    assert _node(out) == "fin" and out.state.facts["hallazgo"].value == {"resumen": "ok"}
    assert port.calls[1].feedback and "5" not in port.calls[1].feedback  # el motivo no repite el dato


def test_a_second_invalid_output_gives_up() -> None:
    w, port, state = _world(_final({"resumen": 5}), _final({"otro": 1}))
    out = w.step(state, agents=port)
    assert _node(out) == "esc" and "hallazgo" not in out.state.facts


def test_degraded_mode_gives_up_without_calling_the_model() -> None:
    w, port, state = _world(_final())
    out = w.step(state, agents=port, degraded=True)
    assert _node(out) == "esc" and port.calls == []
    assert out.state.budgets_used.turn_model_calls == 0


def test_model_call_budget_escalates() -> None:
    steps = [_call() for _ in range(4)]
    w, port, state = _world(*steps, max_steps=5)  # el agente del arnés permite 3 llamadas a modelo por turno
    out = w.step(state, agents=port)
    assert out.stop is Stop.terminal and out.escalation is not None
    assert out.escalation.reason_code == "budget_exceeded" and len(port.calls) == 3


def test_a_missing_agent_port_is_a_wiring_bug() -> None:
    w, _, state = _world(_final())
    with pytest.raises(IllegalTransition):
        w.step(state)


def test_the_loop_is_deterministic() -> None:
    def run() -> list[str]:
        w, port, state = _world(_call(q="x"), _final())
        return [e.type for e in w.step(state, agents=port).events]

    assert run() == run()
    assert Resume().kind == "none"


# --- input_view: what the node exposes to the model (T1) ---------------------------------------------


def test_without_input_view_the_model_gets_no_inputs() -> None:
    w, port, state = _world(_final())
    w.step(state, agents=port)
    assert port.calls[0].inputs == {}


def test_input_view_reaches_the_model_in_model_view_with_wrapped_slots() -> None:
    w = World()
    w.add_tool(tool_def("buscar", source="customers"), handler=lambda a: {})
    port = ScriptedAgent([_call(), _final()])
    f = flow(_agent(input_view=["slots.pregunta", "facts.cliente.value.document_number"]), *TAIL)
    state = w.state(f, slots={"pregunta": slot("¿por qué me cobraron?")},
                    facts={"cliente": fact({"document_number": "12345678"}, ref="buscar@1.0.0")})
    w.step(state, agents=port)
    inputs = port.calls[0].inputs
    assert list(inputs) == ["slots.pregunta", "facts.cliente.value.document_number"]
    text = inputs["slots.pregunta"]
    assert isinstance(text, str) and text.startswith("<datos_no_confiables") and "cobraron" in text
    assert inputs["facts.cliente.value.document_number"] == "⟦doc:1⟧"  # `model` view: the value is tokenized
    assert "12345678" not in repr(port.calls)
    assert port.calls[1].inputs == inputs  # every step gets the same inputs


def _with_inputs(*steps: AgentStepResult, input_view: list[str],
                 **state: Any) -> tuple[World, ScriptedAgent, Any]:
    w = World()
    w.add_tool(tool_def("buscar"), handler=lambda a: {"n": 2})
    port = ScriptedAgent(steps)
    return w, port, w.state(flow(_agent(input_view=input_view), *TAIL), **state)


def test_a_missing_input_gives_up_without_calling_the_model() -> None:
    w, port, state = _with_inputs(_final(), input_view=["slots.pregunta"],
                                  slots={"pregunta": slot("x", "claimed")})  # claimed = absent (D7)
    out = w.step(state, agents=port)
    assert _node(out) == "esc" and port.calls == []
    assert out.state.budgets_used.turn_model_calls == 0


def test_provenance_includes_the_facts_read_by_input_view() -> None:
    w, port, state = _with_inputs(_call(), _final(), input_view=["facts.previo.value.x"],
                                  facts={"previo": fact({"x": 1}, fact_id="fact-0042")})
    out = w.step(state, agents=port)
    (tool_event,) = _types(out, "tool_called")
    assert out.state.facts["hallazgo"].source.inputs == ["fact-0042", tool_event.payload.call_id]


class FailingAgent:
    """`AgentPort` cuyo paso falla con la excepción dada."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def step(self, request: Any, state: Any) -> AgentStepResult:
        raise self.error


def test_t_u5_17_a_gateway_error_ends_the_node_as_gave_up_and_charges_the_reported_usage() -> None:
    w, _, state = _world()
    error = GatewayError(GatewayErrorKind.invalid_output, tokens_in=7, tokens_out=3,
                         cost_usd=Decimal("0.004"), model="m")
    out = w.step(state, agents=FailingAgent(error))
    assert _node(out) == "esc"  # gave_up
    used = out.state.budgets_used
    assert (used.turn_model_calls, used.run_tokens, used.run_cost) == (1, 10, Decimal("0.004"))


def test_t_u5_17_a_gateway_error_without_usage_charges_the_call_only() -> None:
    w, _, state = _world()
    out = w.step(state, agents=FailingAgent(GatewayError(GatewayErrorKind.timeout)))
    used = out.state.budgets_used
    assert _node(out) == "esc"
    assert (used.turn_model_calls, used.run_tokens, used.run_cost) == (1, 0, Decimal("0"))


def test_a_gateway_error_leaves_a_failed_agent_step_that_explains_the_gave_up() -> None:
    w, _, state = _world()
    out = w.step(state, agents=FailingAgent(GatewayError(GatewayErrorKind.timeout)))
    steps = [e for e in out.events if e.type == "agent_step"]
    assert len(steps) == 1
    payload = steps[0].payload
    assert (payload.kind, payload.error_kind, payload.step) == ("failed", GatewayErrorKind.timeout, 1)
    assert payload.tool is None and payload.text_fp is None


def test_t_u5_17_any_other_exception_still_goes_up() -> None:
    w, _, state = _world()
    with pytest.raises(RuntimeError):
        w.step(state, agents=FailingAgent(RuntimeError("error de programación")))
