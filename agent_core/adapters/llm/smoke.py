"""Prueba de humo del gateway (spec §7): prompts sintéticos ES/PT contra el endpoint configurado."""

from dataclasses import dataclass, field
from decimal import Decimal
from math import ceil
from typing import Literal

from agent_core.domain import (
    AgentSelector,
    EntityRef,
    GatewayError,
    JsonValue,
    Locale,
    Principal,
    Prompt,
    RegistryEntity,
    Release,
    SchemaError,
)
from agent_core.ports import Clock, LLMGateway, RegistryPort

SMOKE_PROMPT = EntityRef(id="llm-smoke", version="0.0.0")
_NS_PER_MS = 1_000_000
_SCHEMA: dict[str, JsonValue] = {
    "type": "object", "additionalProperties": False, "required": ["text", "citations"],
    "properties": {"text": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}}}}
_TEXTS: dict[Locale, str] = {
    "es": "Redacta una respuesta breve y cordial para el cliente sobre el asunto indicado. "
          "No inventes cifras.",
    "pt": "Redija uma resposta breve e cordial ao cliente sobre o assunto indicado. Não invente números."}
_TOPICS: dict[Locale, list[str]] = {
    "es": ["cargo desconocido en tienda", "tarjeta bloqueada", "demora de una transferencia",
           "cambio de dirección", "consulta de saldo"],
    "pt": ["cobrança desconhecida em loja", "cartão bloqueado", "atraso em uma transferência",
           "mudança de endereço", "consulta de saldo"]}


@dataclass
class SmokeReport:
    """Resultado agregado de una corrida: conteos, latencias, tokens y costo."""

    calls: int = 0
    ok: int = 0
    errors: dict[str, int] = field(default_factory=dict)
    latencies_ms: list[int] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = Decimal("0")

    @property
    def p50_ms(self) -> int:
        return percentile(self.latencies_ms, 50)

    @property
    def p95_ms(self) -> int:
        return percentile(self.latencies_ms, 95)


def percentile(values: list[int], q: int) -> int:
    """Rango más cercano; 0 si no hay valores."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(ceil(q / 100 * len(ordered)) - 1, 0)]


class SmokeRegistry:
    """`RegistryPort.get` que además sirve el prompt sintético, atado al perfil que se prueba."""

    def __init__(self, inner: RegistryPort, profile: EntityRef) -> None:
        self._inner = inner
        self._prompt = Prompt.model_validate({
            "id": SMOKE_PROMPT.id, "version": SMOKE_PROMPT.version, "locales": dict(_TEXTS),
            "model_profile": f"{profile.id}@{profile.version}"})

    def resolve_release(self, selector: AgentSelector, principal: Principal) -> Release:
        return self._inner.resolve_release(selector, principal)

    def release_status(self, release_id: str) -> Literal["active", "revoked"]:
        return self._inner.release_status(release_id)

    def get[T: RegistryEntity](self, ref: EntityRef, kind: type[T]) -> T:
        if ref == SMOKE_PROMPT:
            if not isinstance(self._prompt, kind):
                raise SchemaError(f"{ref} es un prompt")
            return self._prompt
        return self._inner.get(ref, kind)


def run_smoke(gateway: LLMGateway, prompt: EntityRef, clock: Clock, n: int = 10) -> SmokeReport:
    """Ejecuta `n` generaciones alternando es/pt y agrega latencia, tokens, costo y errores por tipo."""
    report = SmokeReport()
    for i in range(n):
        locale: Locale = "es" if i % 2 == 0 else "pt"
        topic = _TOPICS[locale][(i // 2) % len(_TOPICS[locale])]
        started = clock.monotonic_ns()
        report.calls += 1
        try:
            result = gateway.generate(prompt, {"asunto": topic}, locale, _SCHEMA)
        except GatewayError as error:
            report.errors[error.kind.value] = report.errors.get(error.kind.value, 0) + 1
            report.tokens_in += error.tokens_in or 0
            report.tokens_out += error.tokens_out or 0
            report.cost_usd += error.cost_usd or Decimal("0")
        else:
            report.ok += 1
            report.tokens_in += result.tokens_in
            report.tokens_out += result.tokens_out
            report.cost_usd += result.cost_usd
        report.latencies_ms.append((clock.monotonic_ns() - started) // _NS_PER_MS)
    return report


def format_report(report: SmokeReport) -> str:
    """Texto del reporte; nunca incluye contenido generado ni claves."""
    errors = ", ".join(f"{kind}={count}" for kind, count in sorted(report.errors.items())) or "ninguno"
    rate = report.errors.get("invalid_output", 0) / report.calls if report.calls else 0
    return "\n".join([
        f"llamadas: {report.calls}  ok: {report.ok}  errores: {errors}",
        f"tasa de invalid_output: {rate:.0%}",
        f"latencia p50: {report.p50_ms} ms  p95: {report.p95_ms} ms",
        f"tokens: {report.tokens_in} entrada / {report.tokens_out} salida  costo: {report.cost_usd} USD"])
