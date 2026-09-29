from decimal import Decimal

import pytest

from agent_core.domain import EntityKind, InvalidRuntimeRef, RefSpec, SchemaError
from agent_core.flows import parse_path
from agent_core.interpreter.audit import ViewsAudit, rule_inputs
from agent_core.interpreter.projection import Projector
from agent_core.interpreter.refs import exact_ref, make_resolver
from agent_core.interpreter.resolve import MissingPath
from agent_core.interpreter.templates import render_message
from tests.m02.harness import World, fact, flow, slot, template, tool_def

END = {"id": "fin", "type": "end", "config": {"outcome": "resolved"}}


def _path(text: str):  # type: ignore[no-untyped-def]
    path = parse_path(text)
    assert path is not None
    return path


def test_exact_ref_and_resolver() -> None:
    w = World()
    w.add(template("t/hola", "hola"), tool_def("buscar"))
    ctx = w.ctx()
    assert str(exact_ref(ctx, EntityKind.template, RefSpec.parse("t/hola@1"))) == "t/hola@1.0.0"
    assert str(make_resolver(ctx)(RefSpec.parse("buscar@1"))) == "buscar@1.0.0"
    with pytest.raises(InvalidRuntimeRef):
        exact_ref(ctx, EntityKind.template, RefSpec.parse("t/otra@1"))


def test_model_view_tokenizes_pii_and_keeps_financial_values() -> None:
    w = World()
    state = w.state(flow(END), facts={"cli": fact({"email": "ana@example.com", "amount": Decimal("9.50")})})
    projector = Projector(state, w.ctx())
    assert projector.model_value(_path("facts.cli.value.amount")) == Decimal("9.50")
    token = projector.model_value(_path("facts.cli.value.email"))
    assert isinstance(token, str) and token.startswith("⟦email:") and "ana@example.com" not in token
    audit = projector.audit_value(_path("facts.cli.value.email"))
    assert audit == "***"


def test_slots_claimed_missing_and_wrapped_for_models() -> None:
    w = World()
    state = w.state(flow(END), slots={"s": slot("cargo raro"), "c": slot("x", "claimed")})
    projector = Projector(state, w.ctx())
    with pytest.raises(MissingPath):
        projector.model_value(_path("slots.c"))
    assert projector.audit_value(_path("slots.c")) is None
    plain = projector.model_value(_path("slots.s"))
    wrapped = projector.model_value(_path("slots.s"), wrap_slots=True)
    assert isinstance(plain, str) and plain.startswith("⟦")  # sin clasificar → token (fail closed)
    assert isinstance(wrapped, str) and wrapped.startswith("<datos_no_confiables") and "cargo raro" in wrapped


def test_fact_source_falls_back_when_ref_is_an_action_id() -> None:
    w = World()
    state = w.state(flow(END), facts={"pqr": fact({"id": "pqr-1"}, ref="action-0001")})
    assert Projector(state, w.ctx()).model_value(_path("facts.pqr.value.id")) == "pqr-1"  # `id` es public


def test_render_message_uses_model_view_and_locale() -> None:
    w = World()
    w.add(template("t/pqr", "Radicada {{ facts.pqr.value.id }} por {{ facts.pqr.value.amount }}.",
                   "Registrada {{ facts.pqr.value.id }}."))
    state = w.state(flow(END), facts={"pqr": fact({"id": "pqr-1", "amount": Decimal("10.50")})})
    message = render_message(state, w.ctx(), RefSpec.parse("t/pqr@1"))
    assert (message.kind, message.locale, message.text) == ("template", "es", "Radicada pqr-1 por 10.50.")
    assert render_message(state, w.ctx(locale="pt"), RefSpec.parse("t/pqr@1")).text == "Registrada pqr-1."


def test_render_message_missing_variable_and_locale() -> None:
    w = World()
    w.add(template("t/x", "{{ facts.nope.value.id }}"))
    state = w.state(flow(END))
    with pytest.raises(MissingPath):
        render_message(state, w.ctx(), RefSpec.parse("t/x@1"))
    with pytest.raises(SchemaError):
        render_message(state, w.ctx(locale="fr"), RefSpec.parse("t/x@1"))


def test_views_audit_and_rule_inputs_never_expose_full() -> None:
    w = World()
    state = w.state(flow(END), facts={"m": fact(Decimal("620.00"))}, slots={"s": slot("secreto")})
    reads = ["facts.m.value", "slots.s", "facts.m.value", "slots.ausente", "no.es.ruta"]
    inputs = rule_inputs(state, w.ctx(), reads)
    assert list(inputs) == ["facts.m.value", "slots.s", "slots.ausente"]  # sin duplicados ni no-rutas
    assert "620.00" not in repr(inputs) and "secreto" not in repr(inputs)
    assert inputs["slots.ausente"] is None
    definition = tool_def("buscar", source="tx")
    args = ViewsAudit(w.views, w.vault).args({"email": "ana@example.com"}, definition)
    assert "ana@example.com" not in repr(args)
    result, fingerprint = ViewsAudit(w.views, w.vault).result({"id": "x"}, definition)
    assert fingerprint is not None and result == {"id": "x"}
