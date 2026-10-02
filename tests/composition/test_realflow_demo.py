"""`testing.realflow_demo`: clasificador por palabras y tools con cargos inventados, de punta a punta."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from agent_core.composition.serve import build_api_deps
from agent_core.composition.serve_ports import DemoContext
from agent_core.domain import EntityRef, JsonValue, ProviderSpec, ToolStatus
from agent_core.ports import ToolCallContext
from testing.chat import ChatSession
from testing.engine_world import principal_at, transfer_world
from testing.fakes.identity import TestIdentityIssuer
from testing.realflow_demo import KeywordClassifier, classifier_provider, field_classifier
from testing.realflow_demo import tools as realflow_tools
from tests.composition.test_serve_app import make_ports

SPEC = ProviderSpec(provider="classifier", config={"artifact": "sintetico"})


def _untrusted(source: str, text: str) -> str:
    return f'<datos_no_confiables fuente="{source}">{text}</datos_no_confiables>'


def _entry(agent: str, summary: str, examples: list[str]) -> dict[str, JsonValue]:
    return {"agent_id": agent, "release_id": f"{agent}-demo",
            "summary": _untrusted("directory.entries.summary", summary),
            "examples": [_untrusted("directory.entries.examples", e) for e in examples]}


ENTRIES: list[JsonValue] = [
    _entry("consultas", "Consulta el estado de una PQR ya radicada.",
           ["¿cómo va mi reclamo?", "quiero saber el estado de mi PQR"]),
    _entry("disputas", "Disputa un cargo de la tarjeta que la persona no reconoce.",
           ["no reconozco un cargo", "me cobraron algo que no compré"]),
]
ROUTE_SCHEMA: dict[str, JsonValue] = {"type": "object", "properties": {"choice": {"type": "string"}}}


def _route(problem: str) -> Any:
    inputs: dict[str, JsonValue] = {"slots.problema": _untrusted("slots", problem),
                                    "facts.directorio.value.entries": ENTRIES}
    return KeywordClassifier().predict(SPEC, inputs, ROUTE_SCHEMA, "es")


def test_a_charge_complaint_is_routed_to_disputas_with_confidence() -> None:
    raw = _route("no reconozco un cargo de ciento veinte dólares en una tienda")

    assert raw.value == {"choice": "disputas"}
    assert raw.p_raw["choice"] >= 0.8


def test_a_status_question_is_routed_to_consultas() -> None:
    raw = _route("quiero saber cómo va el estado de mi reclamo")

    assert raw.value == {"choice": "consultas"}
    assert raw.p_raw["choice"] >= 0.8


def test_an_unrelated_message_gets_no_confidence_so_the_flow_clarifies() -> None:
    raw = _route("qué buen clima hace hoy")

    assert raw.p_raw["choice"] == 0.0


MATCH_SCHEMA: dict[str, JsonValue] = {"type": "object", "properties": {
    "match": {"enum": ["unica", "ninguna", "varias"]}, "transaction": {"type": "string"}}}
CHARGES: list[JsonValue] = [
    {"transaction_id": "⟦pii:2⟧", "amount": "120.50", "currency": "USD", "merchant": "Tienda Aurora"},
    {"transaction_id": "⟦pii:3⟧", "amount": "30.00", "currency": "USD", "merchant": "Cafe Sol"},
    {"transaction_id": "⟦pii:4⟧", "amount": "30.00", "currency": "USD", "merchant": "Cine Luna"},
]


def _match(description: str) -> Any:
    inputs: dict[str, JsonValue] = {"slots.descripcion_cargo": _untrusted("slots", description),
                                    "facts.candidatas.value": CHARGES}
    return KeywordClassifier().predict(SPEC, inputs, MATCH_SCHEMA, "es")


def test_a_charge_is_matched_by_its_amount_and_returns_the_token_it_was_shown() -> None:
    raw = _match("el de 120 dólares")

    assert raw.value == {"match": "unica", "transaction": "⟦pii:2⟧"}
    assert raw.p_raw["match"] >= 0.5


def test_a_charge_is_matched_by_its_merchant_even_when_amounts_repeat() -> None:
    raw = _match("el del Cine Luna")

    assert raw.value == {"match": "unica", "transaction": "⟦pii:4⟧"}


def test_two_charges_with_the_same_amount_are_reported_as_several() -> None:
    assert _match("el de 30 dólares").value["match"] == "varias"


def test_no_charge_matching_is_reported_as_none() -> None:
    assert _match("el de 999 dólares").value["match"] == "ninguna"


def _call_context() -> ToolCallContext:
    return ToolCallContext(run_id="run-0001", release="disputas-demo", principal=principal_at("session"))


def test_the_search_tool_returns_invented_charges_with_merchant_and_amount() -> None:
    world = transfer_world()
    tools = realflow_tools(DemoContext(clock=world.clock, ids=world.ids, registry=world.registry))
    ref = EntityRef(id="buscar_transacciones", version="1.0.0")

    result = tools.execute(ref, {"texto": "cualquier cosa"}, {}, _call_context())

    assert result.status is ToolStatus.ok
    charges = result.result_full
    assert isinstance(charges, list) and len(charges) >= 3
    assert all({"transaction_id", "merchant", "amount", "currency"} <= set(c) for c in charges)


def test_a_dispute_goes_from_reception_to_disputas_and_is_filed_end_to_end() -> None:
    world = transfer_world()
    issuer = TestIdentityIssuer(world.clock)
    ctx = DemoContext(clock=world.clock, ids=world.ids, registry=world.registry)
    ports = replace(make_ports(world, issuer), tools=realflow_tools(ctx), classifier=field_classifier(ctx),
                    providers={"jev": world.jev, "classifier": classifier_provider(ctx)})
    client = TestClient(create_app(build_api_deps(ports)), raise_server_exceptions=False)

    def transport(method: str, path: str, headers: Mapping[str, str], body: dict[str, Any] | None
                  ) -> tuple[int, dict[str, Any]]:
        resp = client.request(method, path, headers=dict(headers), json=body)
        return resp.status_code, resp.json()

    lines: list[str] = []
    chat = ChatSession(transport=transport, ids=world.ids, token=issuer.customer, agent="recepcion",
                       out=lines.append, ask=lambda prompt: "sí", step_up_token=issuer.stepped_up)
    chat.start()
    reception_run = chat.run_id
    world.understands("continue")
    world.understands("start_flow", flow="disputa-cargo")
    chat.say("no reconozco un cargo en la Tienda Aurora")
    assert chat.run_id != reception_run  # la transferencia abre un run nuevo y el chat lo sigue
    world.understands("continue")
    chat.say("el de 120 dólares")

    text = "\n".join(lines)
    assert "¿Qué cargo quieres disputar?" in text
    assert chat.closed and "resolved" in text
