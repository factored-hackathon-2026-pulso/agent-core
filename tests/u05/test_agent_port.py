"""`LLMAgentPort`: un paso del nodo `agent` sobre `generate` prompted (spec §3.8; T-U5-13..15)."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from agent_core.adapters.llm import LLMAgentPort
from agent_core.adapters.llm.agent_port import STEP_SCHEMA
from agent_core.domain import (
    AgentNodeConfig,
    EntityRef,
    GatewayError,
    GatewayErrorKind,
    JsonValue,
    ModelPrice,
    ModelProfile,
    Prompt,
    SchemaError,
    StructuredMode,
    ToolDef,
)
from agent_core.interpreter import AgentFinal, AgentObservation, AgentRequest, AgentToolCall
from agent_core.ports import GenerationResult, ToolStatus
from testing.builders import run_state
from testing.fakes.gateway import ScriptedGateway
from testing.fakes.registry import InMemoryRegistry

ARGS_SCHEMA: dict[str, JsonValue] = {"type": "object", "properties": {"q": {"type": "string"}}}
OUTPUT_SCHEMA: dict[str, JsonValue] = {"type": "object", "properties": {"resumen": {"type": "string"}}}


def _step(output: JsonValue, *, tokens_in: int = 40, tokens_out: int = 10) -> GenerationResult:
    return GenerationResult(output=output, tokens_in=tokens_in, tokens_out=tokens_out,
                            cost_usd=Decimal("0.001"), model="scripted-1")


def _profile(structured: StructuredMode = StructuredMode.prompted) -> ModelProfile:
    return ModelProfile(
        id="perfil", version="1.0.0", endpoint_alias="openrouter", model="vendor/modelo-x",
        temperature=Decimal("0.2"), max_tokens=300, timeout_s=8, structured=structured,
        price=ModelPrice(input_per_mtok=Decimal("3.00"), output_per_mtok=Decimal("15.00"),
                         source="prueba", as_of=date(2026, 9, 30)))


def _port(*script: Any, docs: bool = True,
          structured: StructuredMode = StructuredMode.prompted) -> tuple[LLMAgentPort, ScriptedGateway]:
    registry = InMemoryRegistry()
    registry.add(_profile(structured))
    extra: dict[str, Any] = {"description": "Busca un cargo", "args_schema": ARGS_SCHEMA} if docs else {}
    registry.add(
        ToolDef.model_validate({"id": "leer", "version": "1.0.0", "risk_class": "read",
                                "min_auth_level": "session", "idempotent": True, **extra}),
        Prompt.model_validate({"id": "p/agente", "version": "1.0.0", "locales": {"es": "bucle", "pt": "laço"},
                               "model_profile": "perfil@1.0.0"}))
    gateway = ScriptedGateway(script)
    return LLMAgentPort(gateway, registry, lambda kind, ref: ref.require_exact()), gateway


def _request(**over: Any) -> AgentRequest:
    config = AgentNodeConfig.model_validate({
        "tools_allowed": ["leer@1.0.0"], "max_steps": 3, "prompt_ref": "p/agente@1.0.0", "goal": "Investiga",
        "save_as": "hallazgo", "output_schema": OUTPUT_SCHEMA})
    return AgentRequest(node_id="investigar", config=config, step=over.pop("step", 1), **over)


def test_a_tool_call_step_becomes_an_agent_tool_call_with_usage() -> None:  # T-U5-13
    port, _ = _port(_step({"kind": "tool_call", "tool": "leer@1.0.0", "args": {"q": "cargo"}}))
    result = port.step(_request(), run_state())
    assert result.action == AgentToolCall(tool=EntityRef.parse("leer@1.0.0"), args={"q": "cargo"})
    assert (result.model_calls, result.tokens, result.cost_usd) == (1, 50, Decimal("0.001"))


def test_a_final_step_becomes_an_agent_final() -> None:  # T-U5-13
    port, _ = _port(_step({"kind": "final", "output": {"resumen": "ok"}}))
    assert port.step(_request(), run_state()).action == AgentFinal(output={"resumen": "ok"})


def test_the_gateway_gets_the_prompt_locale_step_schema_and_the_catalog() -> None:  # T-U5-14
    port, gateway = _port(_step({"kind": "final", "output": {}}))
    obs = AgentObservation(tool=EntityRef.parse("leer@1.0.0"), args={"q": "x"}, status=ToolStatus.ok,
                           result={"n": 2})
    port.step(_request(step=2, observations=(obs,), feedback="/: falta la propiedad 'resumen'"),
              run_state(locale="pt"))
    (call,) = gateway.calls
    assert str(call.prompt) == "p/agente@1.0.0" and call.locale == "pt" and call.schema == STEP_SCHEMA
    assert call.inputs == {
        "goal": "Investiga", "step": 2, "output_schema": OUTPUT_SCHEMA, "inputs": {},
        "feedback": "/: falta la propiedad 'resumen'",
        "tools": [{"tool": "leer@1.0.0", "description": "Busca un cargo", "args_schema": ARGS_SCHEMA}],
        "observations": [{"tool": "leer@1.0.0", "args": {"q": "x"}, "status": "ok", "result": {"n": 2},
                          "error": None}]}


def test_the_node_inputs_reach_the_model_as_given() -> None:  # T1: what `input_view` declares
    port, gateway = _port(_step({"kind": "final", "output": {}}))
    inputs: dict[str, JsonValue] = {"slots.pregunta": "<datos_no_confiables>¿saldo?</datos_no_confiables>"}
    port.step(_request(inputs=inputs), run_state())
    assert gateway.calls[0].inputs["inputs"] == inputs


def test_a_tool_without_documentation_is_a_programming_error() -> None:  # T-U5-14
    port, gateway = _port(docs=False)
    with pytest.raises(SchemaError):
        port.step(_request(), run_state())
    assert gateway.calls == []


@pytest.mark.parametrize("output", [
    {"kind": "tool_call"},
    {"kind": "tool_call", "tool": "leer"},
    {"kind": "tool_call", "tool": "leer@1.0.0", "args": [1]},
    {"kind": "final"},
    {"kind": "otra"},
    "no es un objeto",
])
def test_a_malformed_step_is_invalid_output_with_the_usage(output: JsonValue) -> None:  # T-U5-15
    port, _ = _port(_step(output, tokens_in=4, tokens_out=2))
    with pytest.raises(GatewayError) as caught:
        port.step(_request(), run_state())
    error = caught.value
    assert error.kind is GatewayErrorKind.invalid_output
    assert (error.tokens_in, error.tokens_out, error.cost_usd, error.model) == (4, 2, Decimal("0.001"),
                                                                                  "scripted-1")


def test_a_tool_outside_tools_allowed_passes_through_for_m2_to_deny() -> None:  # T-U5-15
    port, _ = _port(_step({"kind": "tool_call", "tool": "escribir@1.0.0", "args": {}}))
    assert port.step(_request(), run_state()).action == AgentToolCall(
        tool=EntityRef.parse("escribir@1.0.0"), args={})


def test_a_missing_args_defaults_to_an_empty_object() -> None:
    port, _ = _port(_step({"kind": "tool_call", "tool": "leer@1.0.0"}))
    assert port.step(_request(), run_state()).action == AgentToolCall(
        tool=EntityRef.parse("leer@1.0.0"), args={})


def test_a_gateway_error_goes_up_unchanged() -> None:
    error = GatewayError(GatewayErrorKind.timeout, model="m")
    port, _ = _port(error)
    with pytest.raises(GatewayError) as caught:
        port.step(_request(), run_state())
    assert caught.value is error


def test_a_native_profile_fails_fast_before_calling_the_gateway() -> None:
    port, gateway = _port(_step({"kind": "final", "output": {}}), structured=StructuredMode.native)
    with pytest.raises(SchemaError, match="prompted") as caught:
        port.step(_request(), run_state())
    assert "perfil@1.0.0" in str(caught.value) and "p/agente@1.0.0" in str(caught.value)
    assert gateway.calls == []
