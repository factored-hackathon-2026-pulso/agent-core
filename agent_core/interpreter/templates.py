"""Render de plantillas `{{ ruta }}` en vista `model` (M2 §3.3, D6)."""

from agent_core.domain import EntityKind, Message, RefSpec, RunState, SchemaError, Template
from agent_core.flows import template_vars
from agent_core.interpreter.context import StepContext
from agent_core.interpreter.projection import Projector
from agent_core.interpreter.refs import exact_ref
from agent_core.interpreter.resolve import parse_runtime_path, render_template


def render_message(state: RunState, ctx: StepContext, ref: RefSpec) -> Message:
    template = ctx.registry.get(exact_ref(ctx, EntityKind.template, ref), Template)
    text = template.locales.get(ctx.locale)
    if text is None:
        raise SchemaError(f"la plantilla {template.id} no tiene el locale {ctx.locale}")
    projector = Projector(state, ctx)
    values = {}
    for raw in template_vars(text):
        path = parse_runtime_path(raw)
        assert path is not None  # `template_vars` solo devuelve rutas
        values[raw] = projector.model_value(path)
    return Message(kind="template", text=render_template(text, values), locale=ctx.locale)
