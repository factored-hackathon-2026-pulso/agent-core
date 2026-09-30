"""Dobles de la demo de `agentcore serve` (solo con AGENTCORE_ALLOW_DEMO=1). Cambiar uno es cambiar su ruta.

Cada fábrica recibe un `DemoContext` y devuelve la pieza. Solo datos sintéticos."""

from collections.abc import Callable
from datetime import datetime

from agent_core.composition.serve_ports import DemoContext
from agent_core.decision.calibration.artifact import InMemoryCalibrationSource
from agent_core.domain import EntityRef, JsonValue, ToolDef
from agent_core.ports import ToolCallContext, ToolResult
from agent_core.views import FieldClassifier
from testing.engine_world import _HANDLERS, CATALOG, SyntheticAuthz, _radicar, demo_calibration
from testing.fakes.provider import ScriptedProvider
from testing.fakes.tools import FakeToolExecutor
from testing.fakes.transcript import InMemoryTranscript


class LazyDemoTools:
    """`FakeToolExecutor` que registra cada tool del registry la primera vez que se usa."""

    def __init__(self, ctx: DemoContext) -> None:
        self._inner = FakeToolExecutor(ctx.ids)
        self._registry = ctx.registry
        self._seen: set[EntityRef] = set()

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        if tool not in self._seen:
            definition = self._registry.get(tool, ToolDef)
            if tool.id == "obtener_pqr":
                origin = EntityRef(id="radicar_pqr", version=tool.version)
                self._inner.register_readback(definition, of=origin)
            else:
                self._inner.register(definition, handler=_HANDLERS.get(tool.id, _radicar))
            self._seen.add(tool)
        return self._inner.execute(tool, args, bound_params, ctx, idempotency_key)

    def definition(self, tool: EntityRef) -> ToolDef:
        return self._registry.get(tool, ToolDef)


def tools(ctx: DemoContext) -> LazyDemoTools:
    return LazyDemoTools(ctx)


def authz(ctx: DemoContext) -> SyntheticAuthz:
    return SyntheticAuthz()


def transcript(ctx: DemoContext) -> InMemoryTranscript:
    return InMemoryTranscript()


def calibration(ctx: DemoContext) -> InMemoryCalibrationSource:
    return InMemoryCalibrationSource({"cal-demo": demo_calibration()})


def classifier_provider(ctx: DemoContext) -> ScriptedProvider:
    return ScriptedProvider("classifier")


def field_classifier(ctx: DemoContext) -> FieldClassifier:
    return FieldClassifier(CATALOG)


def grant_active(ctx: DemoContext) -> Callable[[str, datetime], bool]:
    return lambda grant_ref, now: True  # demo: toda asignación firmada vale
