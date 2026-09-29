from datetime import timedelta
from decimal import Decimal

from agent_core.domain import AuthLevel, Decision
from agent_core.interpreter import CircuitBreaker, Resume, Stop
from agent_core.ports import ToolStatus
from testing.builders import NOW, principal
from testing.fakes.tools import Scripted
from tests.m02.harness import SlowTools, World, fact, flow, tool_def

NEXT = {"ok": "fin", "error": "esc", "timeout": "esc", "denied": "esc"}
TAIL = [
    {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}},
]


def _tool(config: dict[str, object]) -> dict[str, object]:
    cfg = {"tool": "buscar@1.0.0", "save_as": "res", **config}
    return {"id": "t", "type": "tool", "config": cfg, "next": NEXT}


def _events(out, kind: str):  # type: ignore[no-untyped-def]
    return [e for e in out.events if e.type == kind]


def _node(out) -> str:  # type: ignore[no-untyped-def]
    return out.state.active_flow.node_id


def test_read_ok_stores_full_value_and_provenance() -> None:
    w = World()
    w.add_tool(tool_def("buscar", source="tx"),
               handler=lambda a: {"echo": a["texto"], "amount": Decimal("9.5")})
    f = flow(_tool({"args": {"texto": "slots.q"}}), *TAIL)
    from tests.m02.harness import slot

    out = w.step(w.state(f, slots={"q": slot("cargo")}))
    stored = out.state.facts["res"]
    assert stored.value == {"echo": "cargo", "amount": Decimal("9.5")}  # vista full en el hecho
    assert (stored.source.kind, stored.source.ref, stored.fact_id) == ("tool", "buscar@1.0.0", "fact-0001")
    assert stored.ts == NOW and _node(out) == "fin"
    (called,) = _events(out, "tool_called")
    assert called.payload.status is ToolStatus.ok and called.payload.attempt == 1
    assert called.payload.args == {"texto": "***"} and called.payload.result_fp is not None
    assert "cargo" not in called.model_dump_json()  # el arg (slot sin clasificar) sale en vista audit
    assert w.tools.calls[0].args == {"texto": "cargo"}  # pero la tool recibe el valor real


def test_t_m2_04_compute_records_provenance_of_inputs() -> None:
    w = World()
    w.add_tool(tool_def("convertir", "compute"), handler=lambda a: a["monto"] * a["tasa"])
    node = _tool({"tool": "convertir@1.0.0", "save_as": "monto_usd", "args": {
        "monto": "facts.tx.value.amount", "moneda": "facts.tx.value.currency", "tasa": "decisions.d.rate",
        "destino": "USD"}})
    decision = Decision(decision_id="decision-0009", value={"rate": Decimal("2")}, p_cal={},
                        provider_used="s", model_version="1")
    state = w.state(flow(node, *TAIL), facts={"tx": fact({"amount": Decimal("10.5"), "currency": "COP"},
                                                          fact_id="fact-0007")},
                    decisions={"d": decision})
    out = w.step(state)
    result = out.state.facts["monto_usd"]
    assert result.value == Decimal("21.0")
    assert (result.source.kind, result.source.ref) == ("compute", "convertir@1.0.0")
    assert result.source.inputs == ["fact-0007", "decision-0009"]


def test_missing_arg_path_takes_error_branch_without_calling() -> None:
    w = World()
    w.add_tool(tool_def("buscar"))
    out = w.step(w.state(flow(_tool({"args": {"texto": "slots.no_existe"}}), *TAIL)))
    assert _node(out) == "esc" and w.tools.calls == [] and _events(out, "tool_called") == []


def test_t_m2_09_retries_only_idempotent_tools() -> None:
    w = World()
    script = [Scripted(ToolStatus.timeout), Scripted(ToolStatus.error),
              Scripted(ToolStatus.ok, result={"a": 1})]
    w.add_tool(tool_def("buscar"), script=script)
    out = w.step(w.state(flow(_tool({}), *TAIL)))
    assert _node(out) == "fin" and len(w.tools.calls) == 3
    assert [e.payload.attempt for e in _events(out, "tool_called")] == [1, 2, 3]

    exhausted = World()
    exhausted.add_tool(tool_def("buscar"), script=[Scripted(ToolStatus.timeout)] * 3)
    out = exhausted.step(exhausted.state(flow(_tool({}), *TAIL)))
    assert _node(out) == "esc" and len(exhausted.tools.calls) == 3 and "res" not in out.state.facts

    single = World()
    single.add_tool(tool_def("buscar", idempotent=False), script=[Scripted(ToolStatus.timeout)])
    out = single.step(single.state(flow(_tool({}), *TAIL)))
    assert _node(out) == "esc" and len(single.tools.calls) == 1


def test_denied_takes_denied_branch_and_emits_access_denied() -> None:
    w = World()
    w.add_tool(tool_def("buscar"), script=[Scripted(ToolStatus.denied)])
    out = w.step(w.state(flow(_tool({}), *TAIL)))
    assert _node(out) == "esc" and len(w.tools.calls) == 1
    (denied,) = _events(out, "access_denied")
    assert denied.payload.reason.value == "tool_denied" and str(denied.payload.tool) == "buscar@1.0.0"


def test_latency_is_measured_with_the_clock() -> None:
    w = World()
    w.add_tool(tool_def("buscar"), handler=lambda a: {"ok": True})
    slow = SlowTools(w.tools, w.clock, timedelta(milliseconds=250))
    out = w.step(w.state(flow(_tool({}), *TAIL)), tools=slow)
    assert _events(out, "tool_called")[0].payload.latency_ms == 250


def test_circuit_breaker_cuts_immediately_and_does_not_retry_the_cut() -> None:
    w = World()
    w.add_tool(tool_def("buscar"), script=[Scripted(ToolStatus.error, error="boom")] * 5)
    out = w.step(w.state(flow(_tool({}), *TAIL)), breaker=CircuitBreaker(threshold=2))
    assert _node(out) == "esc" and len(w.tools.calls) == 2  # el 3.er intento lo corta el breaker
    calls = _events(out, "tool_called")
    assert [(c.payload.status, c.payload.error, c.payload.latency_ms) for c in calls][-1] == (
        ToolStatus.error, "circuit_open", 0)


def test_tool_exception_becomes_error_branch() -> None:
    w = World()
    w.add_tool(tool_def("buscar", idempotent=False))

    class Boom(SlowTools):
        def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("datos sensibles no deben filtrarse")

    out = w.step(w.state(flow(_tool({}), *TAIL)), tools=Boom(w.tools, w.clock, timedelta(0)))
    assert _node(out) == "esc"
    (called,) = _events(out, "tool_called")
    assert called.payload.error == "RuntimeError" and "sensibles" not in called.model_dump_json()


def _step_up_world(max_attempts: int) -> tuple[World, object]:
    w = World()
    w.add_tool(tool_def("buscar", level="step_up"), handler=lambda a: {"ok": True})
    node = _tool({"step_up_max_attempts": max_attempts})
    return w, flow(node, *TAIL)


def test_t_m2_06_step_up_stops_on_same_node_and_retry_continues() -> None:
    w, f = _step_up_world(2)
    first = w.step(w.state(f))  # type: ignore[arg-type]
    assert first.stop is Stop.awaiting_step_up and _node(first) == "t"
    assert first.step_up is not None and first.step_up.required_level is AuthLevel.step_up
    assert first.state.node_attempts == {"t": 1} and w.tools.calls[0].status is ToolStatus.step_up_required
    (requested,) = _events(first, "step_up_requested")
    assert (requested.payload.node_id, requested.payload.attempt) == ("t", 1)
    elevated = first.state.model_copy(update={"principal": principal(auth={"level": "step_up", "at": NOW})})
    second = w.step(elevated, Resume("step_up_retry"))
    assert second.stop is Stop.terminal and _node(second) == "fin"
    assert second.state.node_attempts == {} and "res" in second.state.facts


def test_t_m2_07_step_up_exhausted_escalates_auth_insufficient() -> None:
    w, f = _step_up_world(1)
    first = w.step(w.state(f))  # type: ignore[arg-type]
    assert first.stop is Stop.awaiting_step_up
    second = w.step(first.state, Resume("step_up_retry"))  # sigue sin elevar
    assert second.stop is Stop.terminal and second.escalation is not None
    assert second.escalation.reason_code == "auth_insufficient"
    assert [e.payload.attempt for e in _events(second, "step_up_requested")] == [2]
