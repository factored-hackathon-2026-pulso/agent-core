from decimal import Decimal

import pytest

from agent_core.domain import EntityRef, Template
from agent_core.response.responder import Responder
from agent_core.response.templates import TemplateUnavailable, render_template_text
from testing.fakes.registry import InMemoryRegistry

BY_NAME = {"pqr": {"value": {"id": "pqr-1", "monto": Decimal("1234.56"), "n": 3}}}


def _responder(text_es: str = "Tu disputa {{ facts.pqr.value.id }} quedó radicada.",
               locales: dict[str, str] | None = None) -> Responder:
    registry = InMemoryRegistry()
    registry.add(Template.model_validate({"id": "respaldo", "version": "1.0.0",
                                          "locales": locales or {"es": text_es}}))
    return Responder(registry)


REF = EntityRef.parse("respaldo@1.0.0")


def test_template_renders_facts_and_marks_kind_template() -> None:
    message = _responder().template(REF, "es", BY_NAME)
    assert message.text == "Tu disputa pqr-1 quedó radicada." and message.kind == "template"
    assert message.locale == "es"


def test_missing_locale_raises() -> None:
    with pytest.raises(TemplateUnavailable):
        _responder().template(REF, "pt", BY_NAME)


def test_missing_template_raises() -> None:
    with pytest.raises(TemplateUnavailable):
        _responder().template(EntityRef.parse("otra@1.0.0"), "es", BY_NAME)


def test_missing_fact_path_raises() -> None:
    with pytest.raises(TemplateUnavailable):
        _responder("{{ facts.pqr.value.nada }}").template(REF, "es", BY_NAME)


def test_decimal_renders_without_float() -> None:
    assert render_template_text("Monto {{ facts.pqr.value.monto }}", BY_NAME) == "Monto 1234.56"
    assert render_template_text("N {{facts.pqr.value.n}}", BY_NAME) == "N 3"


@pytest.mark.parametrize("text", [
    "{{ slots.x }}", "{{ decisions.x }}", "{{ facts.pqr.value }}", "{{ facts.pqr.value.id", "{{ }}",
    "{{ facts.pqr.id }}", "{{ facts.pqr.value.id.extra }}",
])
def test_unsupported_or_malformed_expressions_raise(text: str) -> None:
    with pytest.raises(TemplateUnavailable):
        render_template_text(text, BY_NAME)


def test_text_without_expressions_is_returned_as_is() -> None:
    assert render_template_text("Hola", {}) == "Hola"
