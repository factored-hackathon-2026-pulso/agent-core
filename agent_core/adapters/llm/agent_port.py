"""`LLMAgentPort`: un paso del bucle del nodo `agent` sobre `LLMGateway.generate` (spec §3.8, ADR 0019).

Solo hace **un paso**: el bucle, los presupuestos, la ejecución de tools, la validación contra
`output_schema` y la regeneración con `feedback` son de M2. Va en modo `prompted`, sin tool-calling nativo."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from agent_core.domain import (
    EntityKind,
    EntityRef,
    GatewayError,
    GatewayErrorKind,
    InvalidRuntimeRef,
    JsonValue,
    ModelProfile,
    Prompt,
    RefSpec,
    RunState,
    SchemaError,
    StructuredMode,
    ToolDef,
)
from agent_core.interpreter import AgentFinal, AgentObservation, AgentRequest, AgentStepResult, AgentToolCall
from agent_core.ports import GenerationResult, LLMGateway, RegistryPort

# Esquema plano del paso, dentro del subconjunto de `domain.schema` (sin oneOf/anyOf).
STEP_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind"],
    "properties": {
        "kind": {"type": "string", "enum": ["tool_call", "final"]},
        "tool": {"type": "string"},
        "args": {"type": "object"},
        "output": {},
    },
}

ResolveRef = Callable[[EntityKind, RefSpec], EntityRef]


class LLMAgentPort:
    def __init__(self, gateway: LLMGateway, registry: RegistryPort, resolve_ref: ResolveRef) -> None:
        self._gateway = gateway
        self._registry = registry
        self._resolve_ref = resolve_ref

    def step(self, request: AgentRequest, state: RunState) -> AgentStepResult:
        config = request.config
        inputs: dict[str, JsonValue] = {
            "goal": config.goal,
            "step": request.step,
            "tools": [self._catalog_entry(ref) for ref in config.tools_allowed],
            "observations": [_observation(o) for o in request.observations],
            "feedback": request.feedback,
            "output_schema": config.output_schema,
        }
        prompt = self._resolve_ref(EntityKind.prompt, config.prompt_ref)
        self._require_prompted(prompt)
        result = self._gateway.generate(prompt, inputs, state.locale, STEP_SCHEMA)
        return AgentStepResult(
            action=_action(result), model_calls=1, tokens=result.tokens_in + result.tokens_out,
            cost_usd=result.cost_usd)

    def _require_prompted(self, prompt: EntityRef) -> None:
        """Falla rápido si el perfil es `native`: `STEP_SCHEMA` no cabe en `json_schema` estricto de OpenAI."""
        prompt_def = self._registry.get(prompt, Prompt)
        profile_ref = prompt_def.model_profile.require_exact()
        profile = self._registry.get(profile_ref, ModelProfile)
        if profile.structured is StructuredMode.native:
            raise SchemaError(
                f"el prompt {prompt} del nodo agent usa el perfil {profile_ref} con structured=native; "
                "el paso del agente necesita structured=prompted")

    def _catalog_entry(self, ref: RefSpec) -> JsonValue:
        exact = self._resolve_ref(EntityKind.tool, ref)
        tool = self._registry.get(exact, ToolDef)
        if not tool.description or tool.args_schema is None:  # G0-24 lo impide en la validación estática
            raise SchemaError(f"la tool {exact} no tiene description y args_schema")
        return {"tool": str(exact), "description": tool.description, "args_schema": tool.args_schema}


def _observation(o: AgentObservation) -> JsonValue:
    return {"tool": str(o.tool), "args": o.args, "status": o.status.value, "result": o.result,
            "error": o.error}


def _action(result: GenerationResult) -> AgentToolCall | AgentFinal:
    out = result.output
    if isinstance(out, dict):
        if out.get("kind") == "final" and "output" in out:
            return AgentFinal(output=out["output"])
        args = out.get("args", {})
        tool = out.get("tool")
        if out.get("kind") == "tool_call" and isinstance(tool, str) and isinstance(args, dict):
            try:
                return AgentToolCall(tool=EntityRef.parse(tool), args=args)
            except InvalidRuntimeRef:
                pass  # `tool` sin versión exacta: paso inválido
    raise GatewayError(GatewayErrorKind.invalid_output, tokens_in=result.tokens_in,
                       tokens_out=result.tokens_out, cost_usd=result.cost_usd, model=result.model)


if TYPE_CHECKING:
    from agent_core.interpreter import AgentPort

    def _conforms(x: LLMAgentPort) -> AgentPort:
        return x
