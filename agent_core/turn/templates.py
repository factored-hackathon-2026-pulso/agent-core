"""Plantillas del motor (C2): `clarify`, `abstain`, `pending_ack`, `pending_offer`, ... sin variables.

`render_message` de M2 es interno y `flows` está prohibido para M4: se lee `Template.locales[locale]`
con `registry.get`. La versión sale de los pines de la release; si la referencia ya es exacta y la release
no la fija, se usa tal cual."""

from agent_core.domain import EntityKind, Locale, Message, RefSpec, Release, SchemaError, Template
from agent_core.ports import RegistryPort
from agent_core.turn.refs import pinned_ref


def render_engine(registry: RegistryPort, release: Release, ref: RefSpec, locale: Locale) -> Message:
    template = registry.get(pinned_ref(release, EntityKind.template, ref), Template)
    if template.reads:
        raise SchemaError(f"plantilla del motor con variables: {template.id}")
    text = template.locales.get(locale)
    if text is None:
        raise SchemaError(f"la plantilla {template.id} no tiene el locale {locale}")
    return Message(kind="template", text=text, locale=locale)
