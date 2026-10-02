"""Dobles sintéticos para probar el flujo de punta a punta con proveedores reales (LLM y JEV).

Se enchufan en `agentcore serve` con `--tools`, `--classifier`, `--field-classifier` y `--calibration`, solo
con AGENTCORE_ALLOW_DEMO=1. Todo dato es inventado. No son el clasificador de producción (unidad 6)."""

import re
import unicodedata
from decimal import Decimal

from agent_core.composition.serve_ports import DemoContext
from agent_core.decision.calibration.artifact import InMemoryCalibrationSource
from agent_core.decision.types import RawPrediction
from agent_core.domain import EntityRef, JsonValue, ProviderSpec, ToolDef
from agent_core.ports import ToolCallContext, ToolResult
from agent_core.views import FieldClassifier, FieldRule
from testing.engine_world import CATALOG, _radicar, _seleccionar, demo_calibration, transfer_calibration
from testing.fakes.tools import FakeToolExecutor

_WRAPPER = re.compile(r"<datos_no_confiables[^>]*>(.*?)</datos_no_confiables>", re.DOTALL)
_WORD = re.compile(r"[a-z0-9]+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_STOPWORDS = frozenset(
    "a al algo con cual de del el en es la las lo los me mi mis no o para por que quiero se si su un una "
    "y ya como va saber".split())
CONFIDENT = 0.9


def _plain(text: str) -> str:
    match = _WRAPPER.search(text)
    return match.group(1) if match else text


def _words(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", _plain(text).lower())
    ascii_text = "".join(c for c in folded if not unicodedata.combining(c))
    return {w for w in _WORD.findall(ascii_text) if w not in _STOPWORDS and len(w) > 2}


class KeywordClassifier:
    """Doble del proveedor `classifier`: decide por coincidencia de palabras, según el esquema pedido."""

    name = "classifier"

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: str) -> RawPrediction:
        properties = schema.get("properties")
        if isinstance(properties, dict) and "match" in properties:
            return self._match(inputs_model_view)
        return self._route(inputs_model_view)

    def _match(self, inputs: dict[str, JsonValue]) -> RawPrediction:
        description = _plain(str(inputs["slots.descripcion_cargo"]))
        numbers = {Decimal(n.replace(",", ".")) for n in _NUMBER.findall(description)}
        words = _words(description)
        candidates = inputs["facts.candidatas.value"]
        assert isinstance(candidates, list)
        scored: list[tuple[int, str]] = []
        for candidate in candidates:
            assert isinstance(candidate, dict)
            amount = Decimal(str(candidate["amount"]))
            score = 2 if numbers & {amount, amount.to_integral_value(rounding="ROUND_FLOOR")} else 0
            score += len(words & _words(str(candidate.get("merchant", ""))))
            scored.append((score, str(candidate["transaction_id"])))
        top = max((s for s, _ in scored), default=0)
        winners = [t for s, t in scored if s == top]
        if top == 0:
            return RawPrediction(value={"match": "ninguna", "transaction": ""}, p_raw={"match": CONFIDENT})
        if len(winners) > 1:
            return RawPrediction(value={"match": "varias", "transaction": ""}, p_raw={"match": CONFIDENT})
        return RawPrediction(value={"match": "unica", "transaction": winners[0]}, p_raw={"match": CONFIDENT})

    def _route(self, inputs: dict[str, JsonValue]) -> RawPrediction:
        problem = _words(str(inputs["slots.problema"]))
        entries = inputs["facts.directorio.value.entries"]
        assert isinstance(entries, list)
        scores: list[tuple[int, str]] = []
        for entry in entries:
            assert isinstance(entry, dict)
            card = _words(str(entry["summary"]))
            for example in entry.get("examples") or []:  # type: ignore[union-attr]
                card |= _words(str(example))
            scores.append((len(problem & card), str(entry["agent_id"])))
        best = max(scores)
        unique = sum(1 for s, _ in scores if s == best[0]) == 1
        if best[0] == 0 or not unique:
            return RawPrediction(value={"choice": best[1]}, p_raw={"choice": 0.0}, tokens=0)
        return RawPrediction(value={"choice": best[1]}, p_raw={"choice": CONFIDENT}, tokens=0)


# --- datos inventados ---------------------------------------------------------------------------------

CHARGES: list[JsonValue] = [
    {"transaction_id": "tx-1001", "merchant": "Tienda Aurora", "amount": Decimal("120.50"), "currency": "USD",
     "posted_at": "2026-09-27"},
    {"transaction_id": "tx-1002", "merchant": "Cafe Sol", "amount": Decimal("8.75"), "currency": "USD",
     "posted_at": "2026-09-28"},
    {"transaction_id": "tx-1003", "merchant": "Cine Luna", "amount": Decimal("30.00"), "currency": "USD",
     "posted_at": "2026-09-28"},
    {"transaction_id": "tx-1004", "merchant": "Electro Norte", "amount": Decimal("640.00"), "currency": "USD",
     "posted_at": "2026-09-29"},
    {"transaction_id": "tx-1005", "merchant": "Super Central", "amount": Decimal("1850.00"),
     "currency": "MXN", "posted_at": "2026-09-30"},
]
_RATES_TO_USD = {"USD": Decimal("1"), "MXN": Decimal("0.055"), "COP": Decimal("0.00025")}


def _search(args: dict[str, JsonValue]) -> JsonValue:
    return CHARGES


def _convert(args: dict[str, JsonValue]) -> JsonValue:
    rate = _RATES_TO_USD[str(args["moneda"])]
    return (Decimal(str(args["monto"])) * rate).quantize(Decimal("0.01"))


_HANDLERS = {"buscar_transacciones": _search, "seleccionar": _seleccionar, "convertir_moneda": _convert}


class RealflowTools:
    """`FakeToolExecutor` con cargos inventados: registra cada tool del registry la primera vez que se usa."""

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


# --- fábricas para `agentcore serve --tools/--classifier/--field-classifier/--calibration` -------------


def tools(ctx: DemoContext) -> RealflowTools:
    return RealflowTools(ctx)


def classifier_provider(ctx: DemoContext) -> KeywordClassifier:
    return KeywordClassifier()


def field_classifier(ctx: DemoContext) -> FieldClassifier:
    """El catálogo de la demo más los campos del cargo que el emparejador debe poder leer.

    `transaction_id` es `public` porque aquí es un id inventado, y porque el nodo `tool` no destokeniza sus
    argumentos: un id tokenizado devuelto por una decisión no llegaría a `seleccionar`."""
    public = FieldRule(field_class="public")
    return FieldClassifier({**CATALOG, "merchant": public, "posted_at": public, "transaction_id": public})


def calibration(ctx: DemoContext) -> InMemoryCalibrationSource:
    return InMemoryCalibrationSource({"cal-demo": demo_calibration(),
                                      "cal-transfer-demo": transfer_calibration()})
