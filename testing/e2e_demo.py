"""Dobles para la prueba E2E de los tres agentes (cliente, copiloto del asesor y constructor).

Se enchufan en `agentcore serve` con `--tools`, `--classifier`, `--field-classifier` y `--calibration`,
solo con AGENTCORE_ALLOW_DEMO=1. Amplía `testing.realflow_demo` con las lecturas del copiloto; todo dato
es inventado y pertenece al cliente sintético `cust-001`. Las tools `registry/*` del constructor NO pasan
por aquí: `serve --registry-api` las enruta al `BuilderToolExecutor` real (ADR 0019 §4)."""

from decimal import Decimal

from agent_core.composition.serve_ports import DemoContext
from agent_core.decision.calibration.artifact import CalibrationArtifact, InMemoryCalibrationSource
from agent_core.domain import EntityRef, JsonValue, ToolDef
from agent_core.ports import ToolCallContext, ToolResult
from agent_core.views import FieldClassifier, FieldRule
from testing.engine_world import CATALOG, _radicar, demo_calibration, transfer_calibration
from testing.fakes.tools import FakeToolExecutor
from testing.realflow_demo import _HANDLERS as _CLIENT_HANDLERS
from testing.realflow_demo import classifier_provider  # noqa: F401  (se re-exporta como fábrica)

# Más reciente primero: la tool dice «últimos movimientos». El servicio real debe devolverlos así (o con un
# orden explícito), porque el copiloto no ordena por su cuenta de forma fiable.
_MOVEMENTS: list[JsonValue] = [
    {"movement_id": "mv-2", "merchant": "Cafe Sol", "amount": Decimal("8.75"), "currency": "USD",
     "posted_at": "2026-09-28", "status": "posted"},
    {"movement_id": "mv-1", "merchant": "Tienda Aurora", "amount": Decimal("120.50"), "currency": "USD",
     "posted_at": "2026-09-27", "status": "posted"},
    {"movement_id": "mv-4", "merchant": "Restaurante Brasa", "amount": Decimal("41.00"), "currency": "USD",
     "posted_at": "2026-08-21", "status": "posted"},
    {"movement_id": "mv-3", "merchant": "Restaurante Mar", "amount": Decimal("64.20"), "currency": "USD",
     "posted_at": "2026-08-14", "status": "posted"},
]
_PRODUCTS: list[JsonValue] = [
    {"product": "Tarjeta Oro", "balance_due": Decimal("1342.80"), "credit_limit": Decimal("5000.00"),
     "minimum_payment": Decimal("67.00"), "currency": "USD", "due_date": "2026-10-20"},
]
_CASES: list[JsonValue] = [
    {"case_id": "pqr-demo-1", "status": "Open", "topic": "disputa de cargo"},
]
_HANDOFF: JsonValue = {
    "reason": "monto sobre el umbral de disputas", "summary": "El cliente no reconoce un cargo de 640 USD "
    "en Electro Norte; el agente de atención escaló por política de monto.", "target_queue": "disputas"}
_TRANSCRIPT: list[JsonValue] = [
    {"speaker": "cliente", "text": "No reconozco un cargo de Electro Norte."},
    {"speaker": "agente", "text": "Entiendo, voy a revisar ese cargo con un especialista."},
]

_HANDLERS = {
    **_CLIENT_HANDLERS,
    "leer_movimientos": lambda args: _MOVEMENTS,
    "leer_productos": lambda args: _PRODUCTS,
    "leer_pqr_cliente": lambda args: _CASES,
    "obtener_handoff": lambda args: _HANDOFF,
    "leer_transcript": lambda args: _TRANSCRIPT,
}


class E2ETools:
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


def tools(ctx: DemoContext) -> E2ETools:
    return E2ETools(ctx)


def field_classifier(ctx: DemoContext) -> FieldClassifier:
    """El catálogo de la demo más los campos que lee el copiloto. Los numéricos y los ids son `public` porque
    son datos inventados; el texto libre (conversación, resumen del traspaso) es `untrusted_text`: el modelo
    lo ve envuelto y no como instrucción. El catálogo real lo publica el equipo de datos (m07)."""
    public = FieldRule(field_class="public")
    untrusted = FieldRule(field_class="untrusted_text")
    extra = ("merchant", "posted_at", "transaction_id", "movement_id", "product", "balance_due",
             "credit_limit", "minimum_payment", "due_date", "case_id", "topic", "target_queue", "speaker")
    free_text = ("reason", "summary", "text")
    # Salida del nodo `agent` del copiloto (flow asistir, `output_schema`). M7 tokeniza todo campo sin
    # clasificar, y eso dejaba al `respond` sin datos («⟦pii:1⟧»). Las cifras (`valor`) y las fechas ISO
    # (`fecha`) tienen que llegar tal cual: M8 §3.3 solo respalda una cifra con un valor numérico o una fecha
    # ISO del hecho citado. `moneda` y `fuente` son enums del esquema. El texto que escribió el modelo
    # (`resumen`, `concepto`, `detalle`) viaja envuelto como `untrusted_text`: se deriva de texto del
    # cliente. Se clasifican por nombre porque M7 proyecta con la fuente `agent` y M8 (`find_clear_pii`)
    # con el nombre del hecho.
    agent_output = {"valor": FieldRule(field_class="financial"), "moneda": public, "fecha": public,
                    "fuente": public, "resumen": untrusted, "concepto": untrusted, "detalle": untrusted}
    return FieldClassifier({**CATALOG, **dict.fromkeys(extra, public), **dict.fromkeys(free_text, untrusted),
                            **agent_output})


def _with_portuguese(artifact: CalibrationArtifact) -> CalibrationArtifact:
    """Los umbrales se buscan por idioma: sin una entrada `pt`, todo mensaje en portugués queda bajo umbral y
    el agente solo pide aclarar. Hecho a mano para la demo (la calibración real es de la unidad 6)."""
    thresholds = dict(artifact.thresholds)
    for key, value in artifact.thresholds.items():
        if key[-1] == "es":
            thresholds[(*key[:-1], "pt")] = value
    return artifact.model_copy(update={"thresholds": thresholds})


def calibration(ctx: DemoContext) -> InMemoryCalibrationSource:
    return InMemoryCalibrationSource({"cal-demo": _with_portuguese(demo_calibration()),
                                      "cal-transfer-demo": _with_portuguese(transfer_calibration())})
