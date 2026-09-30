"""Prueba de humo del gateway (spec §7): prompts sintéticos ES/PT contra el endpoint configurado."""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from math import ceil
from typing import Literal

from agent_core.domain import (
    AgentNodeConfig,
    AgentSelector,
    EntityRef,
    GatewayError,
    GatewayErrorKind,
    JsonValue,
    Locale,
    Principal,
    Prompt,
    RegistryEntity,
    Release,
    SchemaError,
    ToolDef,
)
from agent_core.ports import Clock, LLMGateway, RegistryPort

SMOKE_PROMPT = EntityRef(id="llm-smoke", version="0.0.0")
SMOKE_AGENT_PROMPT = EntityRef(id="llm-smoke-agente", version="0.0.0")
SMOKE_TOOL = EntityRef(id="smoke-buscar", version="0.0.0")
AGENT_STEPS = 3
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
_AGENT_TEXTS: dict[Locale, str] = {
    "es": "Eres un agente que resuelve un objetivo en pasos. En cada paso recibes el objetivo, las tools "
          "disponibles y las observaciones previas. Responde con un objeto JSON "
          '{"kind": "tool_call" | "final", "tool": "id@versión", "args": {...}, "output": ...}: '
          'usa kind "tool_call" con tool y args para consultar una tool, o kind "final" con output '
          "cuando ya puedas responder. No inventes datos.",
    "pt": "Você é um agente que resolve um objetivo em passos. A cada passo recebe o objetivo, as tools "
          "disponíveis e as observações anteriores. Responda com um objeto JSON "
          '{"kind": "tool_call" | "final", "tool": "id@versão", "args": {...}, "output": ...}: '
          'use kind "tool_call" com tool e args para consultar uma tool, ou kind "final" com output '
          "quando já puder responder. Não invente dados."}


def smoke_agent_config() -> AgentNodeConfig:
    """Configuración sintética del nodo `agent` que usan los pasos de la prueba de humo."""
    return AgentNodeConfig.model_validate({
        "tools_allowed": [str(SMOKE_TOOL)], "max_steps": AGENT_STEPS, "prompt_ref": str(SMOKE_AGENT_PROMPT),
        "goal": "Averigua el estado de un cargo desconocido en tienda", "save_as": "hallazgo",
        "output_schema": {"type": "object", "properties": {"resumen": {"type": "string"}}}})


@dataclass(frozen=True)
class AgentStepUsage:
    """Uso de un paso de agente que terminó bien."""

    tokens: int
    cost_usd: Decimal


# Ejecuta el paso `step` (desde 1) del nodo `agent` sintético y lanza `GatewayError` si falla. Lo arma
# `cli` con `LLMAgentPort`: este módulo no importa `interpreter` (import-linter).
AgentStepRunner = Callable[[int, Locale], AgentStepUsage]


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
    agent_steps: int = 0
    agent_ok: int = 0
    agent_invalid: int = 0
    agent_other_errors: int = 0
    agent_tokens: int = 0
    agent_cost_usd: Decimal = Decimal("0")

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
        bound = f"{profile.id}@{profile.version}"
        self._served: dict[EntityRef, RegistryEntity] = {
            SMOKE_PROMPT: Prompt.model_validate({
                "id": SMOKE_PROMPT.id, "version": SMOKE_PROMPT.version, "locales": dict(_TEXTS),
                "model_profile": bound}),
            SMOKE_AGENT_PROMPT: Prompt.model_validate({
                "id": SMOKE_AGENT_PROMPT.id, "version": SMOKE_AGENT_PROMPT.version,
                "locales": dict(_AGENT_TEXTS), "model_profile": bound}),
            SMOKE_TOOL: ToolDef.model_validate({
                "id": SMOKE_TOOL.id, "version": SMOKE_TOOL.version, "risk_class": "read",
                "min_auth_level": "session", "idempotent": True,
                "description": "Busca el estado de un cargo por su descripción",
                "args_schema": {"type": "object", "properties": {"consulta": {"type": "string"}}}})}

    def resolve_release(self, selector: AgentSelector, principal: Principal) -> Release:
        return self._inner.resolve_release(selector, principal)

    def release_status(self, release_id: str) -> Literal["active", "revoked"]:
        return self._inner.release_status(release_id)

    def get[T: RegistryEntity](self, ref: EntityRef, kind: type[T]) -> T:
        entity = self._served.get(ref)
        if entity is not None:
            if not isinstance(entity, kind):
                raise SchemaError(f"{ref} no es de la clase pedida")
            return entity
        return self._inner.get(ref, kind)


def run_smoke(gateway: LLMGateway, prompt: EntityRef, clock: Clock, n: int = 10,
              agent_step: AgentStepRunner | None = None) -> SmokeReport:
    """Ejecuta `n` generaciones alternando es/pt y agrega latencia, tokens, costo y errores por tipo.

    Con `agent_step`, corre además 3 pasos del nodo `agent` y cuenta los pasos inválidos (spec §7)."""
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
    if agent_step is not None:
        _run_agent_steps(report, agent_step)
    return report


def _run_agent_steps(report: SmokeReport, agent_step: AgentStepRunner) -> None:
    """Los pasos del agente no ejecutan tools: miden solo si el modelo devuelve un paso bien formado."""
    for step in range(1, AGENT_STEPS + 1):
        report.agent_steps += 1
        try:
            usage = agent_step(step, "es")
        except GatewayError as error:
            if error.kind is GatewayErrorKind.invalid_output:
                report.agent_invalid += 1
            else:
                report.agent_other_errors += 1
            report.agent_tokens += (error.tokens_in or 0) + (error.tokens_out or 0)
            report.agent_cost_usd += error.cost_usd or Decimal("0")
        else:
            report.agent_ok += 1
            report.agent_tokens += usage.tokens
            report.agent_cost_usd += usage.cost_usd


def format_report(report: SmokeReport) -> str:
    """Texto del reporte; nunca incluye contenido generado ni claves."""
    errors = ", ".join(f"{kind}={count}" for kind, count in sorted(report.errors.items())) or "ninguno"
    rate = report.errors.get("invalid_output", 0) / report.calls if report.calls else 0
    lines = [
        f"llamadas: {report.calls}  ok: {report.ok}  errores: {errors}",
        f"tasa de invalid_output: {rate:.0%}",
        f"latencia p50: {report.p50_ms} ms  p95: {report.p95_ms} ms",
        f"tokens: {report.tokens_in} entrada / {report.tokens_out} salida  costo: {report.cost_usd} USD"]
    if report.agent_steps:
        invalid = report.agent_invalid / report.agent_steps
        lines.append(
            f"pasos agent: {report.agent_steps}  ok: {report.agent_ok}  inválidos: {report.agent_invalid} "
            f"({invalid:.0%})  otros errores: {report.agent_other_errors}  tokens: {report.agent_tokens}  "
            f"costo: {report.agent_cost_usd} USD")
    return "\n".join(lines)
