"""Plantillas del motor (C2): `registry.get` sin variables."""

import pytest

from agent_core.domain import Release, SchemaError, Template
from agent_core.domain.refs import RefSpec
from agent_core.turn.templates import render_engine
from testing.fakes.registry import InMemoryRegistry


def release(**templates: str) -> Release:
    return Release.model_validate(
        {
            "id": "rel-1",
            "status": "active",
            "entities": {"template": templates},
            "language_detection": "lang@1.0.0",
        }
    )


def registry(*templates: Template) -> InMemoryRegistry:
    reg = InMemoryRegistry()
    reg.add(*templates)
    return reg


def test_plantilla_del_motor_en_el_locale_y_sin_variables() -> None:
    reg = registry(
        Template(
            id="t-aclarar", version="1.0.0", locales={"es": "¿Puedes aclarar?", "pt": "Pode esclarecer?"}
        )
    )
    msg = render_engine(reg, release(**{"t-aclarar": "1.0.0"}), RefSpec.parse("t-aclarar@1"), "pt")
    assert (msg.kind, msg.text, msg.locale) == ("template", "Pode esclarecer?", "pt")


def test_usa_la_version_fijada_por_la_release_y_no_la_de_la_referencia() -> None:
    reg = registry(
        Template(id="t-x", version="1.0.0", locales={"es": "viejo"}),
        Template(id="t-x", version="1.1.0", locales={"es": "nuevo"}),
    )
    msg = render_engine(reg, release(**{"t-x": "1.1.0"}), RefSpec.parse("t-x@1"), "es")
    assert msg.text == "nuevo"


def test_referencia_exacta_sin_pin_en_la_release_se_usa_tal_cual() -> None:
    reg = registry(Template(id="t-x", version="1.0.0", locales={"es": "hola"}))
    assert render_engine(reg, release(), RefSpec.parse("t-x@1.0.0"), "es").text == "hola"


def test_plantilla_con_reads_es_error_de_configuracion() -> None:
    reg = registry(
        Template(
            id="t-x", version="1.0.0", locales={"es": "hola {{ facts.a }}"}, reads=frozenset({"facts.a"})
        )
    )
    with pytest.raises(SchemaError):
        render_engine(reg, release(**{"t-x": "1.0.0"}), RefSpec.parse("t-x@1"), "es")


def test_locale_ausente_es_error_de_configuracion() -> None:
    reg = registry(Template(id="t-x", version="1.0.0", locales={"es": "hola"}))
    with pytest.raises(SchemaError):
        render_engine(reg, release(**{"t-x": "1.0.0"}), RefSpec.parse("t-x@1"), "pt")


def test_plantilla_sin_pin_ni_version_exacta_es_error_de_configuracion() -> None:
    reg = registry(Template(id="t-x", version="1.0.0", locales={"es": "hola"}))
    with pytest.raises(SchemaError):
        render_engine(reg, release(), RefSpec.parse("t-x@1"), "es")
