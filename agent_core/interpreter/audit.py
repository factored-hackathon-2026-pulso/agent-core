"""Vista `audit` para eventos de M2 y para `ActionContext.audit` de M3 (M7 es la única salida de `full`)."""

from agent_core.domain import Fingerprint, JsonValue, RunState, ToolDef
from agent_core.interpreter.context import StepContext
from agent_core.interpreter.projection import Projector
from agent_core.interpreter.resolve import MissingPath, parse_runtime_path
from agent_core.views import TokenVault, ViewService


class ViewsAudit:
    """`AuditProjector` de M3: argumentos y resultados de una tool en vista `audit` + huella con clave."""

    def __init__(self, views: ViewService, vault: TokenVault) -> None:
        self._views = views
        self._vault = vault

    @staticmethod
    def _source(tool: ToolDef) -> str:
        return tool.source or tool.id

    def args(self, args: dict[str, JsonValue], tool: ToolDef) -> dict[str, JsonValue]:
        audit = self._views.project(args, self._source(tool), tool.untrusted_fields, self._vault).audit
        return audit if isinstance(audit, dict) else {}

    def result(self, result_full: JsonValue, tool: ToolDef) -> tuple[JsonValue, Fingerprint | None]:
        views = self._views.project(result_full, self._source(tool), tool.untrusted_fields, self._vault)
        return views.audit, views.fingerprint


def rule_inputs(state: RunState, ctx: StepContext, reads: list[str]) -> dict[str, JsonValue]:
    """Entradas de un `rule` en vista `audit`: solo las rutas `slots.*`/`facts.*` que la expresión leyó."""
    projector = Projector(state, ctx)
    inputs: dict[str, JsonValue] = {}
    for raw in dict.fromkeys(reads):
        try:
            path = parse_runtime_path(raw)
        except MissingPath:
            continue
        if path is not None and path.ns in ("slots", "facts"):
            inputs[raw] = projector.audit_value(path)
    return inputs
