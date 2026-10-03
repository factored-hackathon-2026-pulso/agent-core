"""Nodo `agent` de M2 with `LLMAgentPort` over `HttpLLMGateway` (spec §3.8; T-U5-16)."""

from decimal import Decimal

import httpx
from respx import MockRouter

from agent_core.adapters.llm import HttpLLMGateway, LLMAgentPort
from agent_core.domain import Prompt, ToolDef
from tests.gateway_http.helpers import BASE_URL, GENERATE, TOKEN, install_llm_entities, success
from tests.m02.harness import World, flow

SCHEMA = {"type": "object", "properties": {"resumen": {"type": "string"}}, "required": ["resumen"],
          "additionalProperties": False}
TAIL = [{"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
        {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}}]
AGENT = {"id": "a", "type": "agent", "next": {"answered": "fin", "gave_up": "esc"},
         "config": {"tools_allowed": ["buscar@1.0.0"], "max_steps": 3, "prompt_ref": "p/x@1.0.0",
                    "goal": "g", "save_as": "hallazgo", "output_schema": SCHEMA}}


def _world() -> tuple[World, LLMAgentPort]:
    w = World()
    w.add_tool(
        ToolDef.model_validate({"id": "buscar", "version": "1.0.0", "risk_class": "read",
                                "min_auth_level": "session", "idempotent": True, "source": "tx",
                                "description": "Busca un cargo", "args_schema": {"type": "object"}}),
        handler=lambda a: {"n": 2})
    install_llm_entities(w.registry)
    w.registry.add(Prompt.model_validate(
        {"id": "p/x", "version": "1.0.0", "locales": {"es": "bucle"}, "model_profile": "perfil@1.0.0"}))
    gateway = HttpLLMGateway(w.registry, BASE_URL, TOKEN)
    port = LLMAgentPort(gateway, w.registry, lambda kind, ref: ref.require_exact())
    return w, port


def test_t_u5_16_el_bucle_llega_a_answered_y_acumula_el_costo(respx_mock: MockRouter) -> None:
    respx_mock.post(GENERATE).mock(side_effect=[
        httpx.Response(200, text=success({"kind": "tool_call", "tool": "buscar@1.0.0", "args": {}})),
        httpx.Response(200, text=success({"kind": "final", "output": {"resumen": "ok"}}))])
    w, port = _world()
    out = w.step(w.state(flow(AGENT, *TAIL)), agents=port)
    assert out.state.active_flow.node_id == "fin"
    assert out.state.facts["hallazgo"].value == {"resumen": "ok"}
    used = out.state.budgets_used
    assert used.turn_model_calls == 2 and used.run_tokens == 300
    assert used.run_cost == Decimal("0.001620")  # 2 x (120 x 3 + 30 x 15) / 1e6
