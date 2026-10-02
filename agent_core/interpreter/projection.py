"""Puente hacia M7: vistas `model` y `audit` de slots y hechos del run. `full` solo sale por `ctx.views`."""

from agent_core.domain import EntityRef, Fact, InvalidRuntimeRef, JsonValue, RunState, ToolDef
from agent_core.flows import Path
from agent_core.interpreter.context import StepContext
from agent_core.interpreter.resolve import MissingPath, parse_runtime_path, resolve_path, walk
from agent_core.views import Views


def model_inputs(paths: list[str], state: RunState, ctx: StepContext) -> dict[str, JsonValue]:
    """Input of a model (`decide`, `agent`): each path in the `model` view, with slots wrapped (D8).

    Raises `MissingPath` if a path does not resolve; each node decides which branch to take."""
    projector = Projector(state, ctx)
    inputs: dict[str, JsonValue] = {}
    for raw in paths:
        path = parse_runtime_path(raw)
        if path is None:
            raise MissingPath(raw)
        inputs[raw] = projector.model_value(path, wrap_slots=True)
    return inputs


class Projector:
    """Proyecta cada hecho una sola vez por instancia (el `TokenVault` da el mismo token al mismo valor)."""

    def __init__(self, state: RunState, ctx: StepContext) -> None:
        self._state = state
        self._ctx = ctx
        self._facts: dict[str, Views] = {}

    def model_value(self, path: Path, *, wrap_slots: bool = False) -> JsonValue:
        return self._value(path, "model", wrap_slots)

    def audit_value(self, path: Path) -> JsonValue:
        try:
            return self._value(path, "audit", False)
        except MissingPath:
            return None

    def _value(self, path: Path, view: str, wrap_slots: bool) -> JsonValue:
        name = path.name or ""
        if path.ns == "facts":
            if not path.rest or path.rest[0] != "value":
                raise MissingPath(path.raw)
            views = self._fact_views(name)
            return walk(views.model if view == "model" else views.audit, path.rest[1:], path.raw)
        if path.ns == "slots":
            found = self._state.slots.get(name)
            if found is None or found.status != "validated":
                raise MissingPath(path.raw)
            untrusted = ["slots"] if wrap_slots else []  # D8
            views = self._ctx.views.project(found.value, "slots", untrusted, self._ctx.vault)
            return views.model if view == "model" else views.audit
        return resolve_path(self._state, path)  # decisions: ya se calcularon sobre la vista `model`

    def _fact_views(self, name: str) -> Views:
        cached = self._facts.get(name)
        if cached is not None:
            return cached
        fact = self._state.facts.get(name)
        if fact is None:
            raise MissingPath(f"facts.{name}")
        source, untrusted = self._source(fact)
        views = self._ctx.views.project(fact.value, source, untrusted, self._ctx.vault)
        self._facts[name] = views
        return views

    def _source(self, fact: Fact) -> tuple[str, list[str]]:
        if fact.source.kind == "agent":
            # Salida del modelo: sus campos se clasifican por nombre (M7); los que no lo estén se tokenizan.
            return "agent", []
        if fact.source.kind in ("tool", "compute"):
            definition = self._tool_def(fact.source.ref)
            if definition is not None:
                return definition.source or definition.id, definition.untrusted_fields
        return fact.source.ref, []

    def _tool_def(self, ref: str) -> ToolDef | None:
        """`ref` es `tool@X.Y.Z`, o un `action_id` (los hechos de M3 de escritura y readback)."""
        try:
            return self._ctx.tools.definition(EntityRef.parse(ref))
        except (InvalidRuntimeRef, KeyError):
            pass
        for action in self._state.actions:
            if action.action_id == ref:
                try:
                    return self._ctx.tools.definition(action.tool)
                except KeyError:
                    return None
        return None
