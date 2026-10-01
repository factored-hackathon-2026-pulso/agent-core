"""Esquemas del catálogo cerrado de nodos (M0 §2.5; spec general §5).

Validan forma, no semántica del grafo: alcanzabilidad, dominancia, reclamos y cobertura de `next` son reglas
G0 de M1. En YAML el nodo de escritura sigue siendo `type: tool`; el discriminador lo reconoce por
`action_from`.

Los nodos son datos de autoría: `expr`, `predicate`, `priority_expr` y los `args` se guardan tal cual y M0
nunca los evalúa. Un `type` desconocido o ausente no tiene etiqueta y falla (cierra por defecto).
"""

from collections.abc import Mapping
from datetime import timedelta
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    Discriminator,
    Field,
    PositiveInt,
    StringConstraints,
    Tag,
    model_validator,
)

from agent_core.domain.base import Model, NodeId, SaveAs
from agent_core.domain.identity import PrincipalType
from agent_core.domain.json import JsonValue
from agent_core.domain.knowledge import PagePath, Purpose, parse_page_spec
from agent_core.domain.outcomes import Outcome, ReasonCodeStr
from agent_core.domain.refs import RefSpec


def _positive(value: timedelta) -> timedelta:
    if value <= timedelta(0):
        raise ValueError("la duración debe ser positiva")
    return value


PositiveTimedelta = Annotated[timedelta, AfterValidator(_positive)]


class SlotValidator(Model):
    """Validador de un slot recolectado (M0 §2.5)."""
    kind: Literal["type", "regex", "enum", "decide"]
    value: JsonValue


class DecideConfig(Model):
    """Resultados = valores del enum de `branch_on` + `low_confidence`, cableados en `next` (rev. 4)."""

    model: RefSpec
    input_view: list[str] | None = None
    branch_on: str
    save_as: SaveAs


class RuleConfig(Model):
    """Configuración del nodo `rule` (M0 §2.5)."""
    policy: RefSpec | None = None
    expr: JsonValue = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "RuleConfig":
        if (self.policy is None) == (self.expr is None):
            raise ValueError("rule declara exactamente uno: policy o expr")
        return self


class CollectConfig(Model):
    """Configuración del nodo `collect` (M0 §2.5)."""
    slot: str
    prompt_ref: RefSpec
    validator: SlotValidator | None = None  # None: texto no vacío
    max_attempts: PositiveInt = 2


class ToolConfig(Model):
    """Configuración del nodo `tool` (M0 §2.5)."""
    tool: RefSpec
    args: dict[str, JsonValue] = Field(default_factory=dict)
    save_as: SaveAs
    step_up_max_attempts: PositiveInt = 2


class WriteToolConfig(Model):
    """Escritura: sin `args` propios; usa la acción congelada del `confirm` (ADR 0007)."""

    action_from: NodeId
    save_as: SaveAs
    step_up_max_attempts: PositiveInt = 2


class ActionSpec(Model):
    """Acción de escritura que propone un nodo `confirm`: tool y argumentos (M0 §2.5)."""
    tool: RefSpec
    args: dict[str, JsonValue] = Field(default_factory=dict)


class ConfirmConfig(Model):
    """Configuración del nodo `confirm` (M0 §2.5)."""
    action: ActionSpec
    summary_template: RefSpec
    reprompt_template: RefSpec | None = None
    max_attempts: PositiveInt = 2


class VerifyConfig(Model):
    """Configuración del nodo `verify` (M0 §2.5)."""
    readback: RefSpec
    by: Annotated[str, StringConstraints(pattern=r"^(idempotency_key|fact:.+)$")]
    predicate: JsonValue
    save_as: SaveAs


class GenerateConfig(Model):
    """Configuración del nodo `generate` (M0 §2.5).

    `knowledge_from` lista los `save_as` de nodos `knowledge` cuyas páginas puede citar la respuesta, y
    `purpose` dice para quién es (M12 §2: reemplaza a `knowledge_refs`). El defecto es el más estricto."""
    prompt_ref: RefSpec
    allowed_facts: list[str] = Field(default_factory=list)
    knowledge_from: list[SaveAs] = Field(default_factory=list)
    purpose: Purpose = "customer_answer"
    fallback_template_ref: RefSpec


class RespondConfig(Model):
    """Configuración del nodo `respond` (M0 §2.5)."""
    template_ref: RefSpec | None = None
    generate: GenerateConfig | None = None
    await_: bool = Field(default=False, alias="await")
    claims: list[NodeId] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_one(self) -> "RespondConfig":
        if (self.template_ref is None) == (self.generate is None):
            raise ValueError("respond declara exactamente uno: template_ref o generate")
        return self


class EscalateConfig(Model):
    """Configuración del nodo `escalate` (M0 §2.5)."""
    reason_code: ReasonCodeStr
    target_queue: str | None = None  # None: agent.default_target_queue
    priority_expr: JsonValue = None


class EndConfig(Model):
    """Configuración del nodo `end` (M0 §2.5)."""
    outcome: Outcome
    output_map: dict[str, str] | None = None


def _page_specs(pages: list[str]) -> list[str]:
    for text in pages:
        parse_page_spec(text)
    return pages


class KnowledgeConfig(Model):
    """Configuración del nodo `knowledge` (M0 §2.5, M12, ADR 0015).

    `read`: `pages` fijas (`ruta` o `ruta#ancla`), resueltas contra el snapshot de la release.
    `navigate`: `scope` (un directorio del snapshot) y `selector` (un `decision_model` sobre las rutas del
    scope); el modo está en el esquema, pero M12 todavía no lo ejecuta (sale por `not_found`)."""

    mode: Literal["read", "navigate"]
    pages: Annotated[list[str], AfterValidator(_page_specs)] = Field(default_factory=list)
    scope: PagePath | None = None
    selector: RefSpec | None = None
    purpose: Purpose
    save_as: SaveAs

    @model_validator(mode="after")
    def _shape_of_the_mode(self) -> "KnowledgeConfig":
        if self.mode == "read":
            if not self.pages:
                raise ValueError("knowledge read necesita al menos una página")
            if self.scope is not None or self.selector is not None:
                raise ValueError("scope y selector son solo de navigate")
        else:
            if self.scope is None or self.selector is None:
                raise ValueError("knowledge navigate necesita scope y selector")
            if self.pages:
                raise ValueError("pages es solo de read")
        return self


class AgentNodeConfig(Model):
    """Configuración del nodo `agent` (M0 §2.5, ADR 0019).

    `save_as` nombra el hecho donde entra la salida; `output_schema` es el JSON Schema de esa salida.
    `input_view` son las rutas (`slots`, `facts`) que el modelo ve en vista `model`; vacío, no ve ninguna."""
    tools_allowed: list[RefSpec]
    max_steps: PositiveInt
    prompt_ref: RefSpec
    goal: str
    save_as: SaveAs
    output_schema: dict[str, JsonValue]
    input_view: list[str] = Field(default_factory=list)


class SubflowConfig(Model):
    """Configuración del nodo `subflow` (M0 §2.5)."""
    flow: RefSpec
    map_in: dict[str, str] = Field(default_factory=dict)
    map_out: dict[str, str] = Field(default_factory=dict)


class Approver(Model):
    """Aprobador de un nodo `await_approval` (M0 §2.5)."""
    principal_type: PrincipalType
    roles: list[str] = Field(default_factory=list)


class AwaitApprovalConfig(Model):
    """Configuración del nodo `await_approval` (M0 §2.5)."""
    approver: Approver
    summary_template: RefSpec
    timeout: PositiveTimedelta


class _NodeBase(Model):
    id: NodeId
    next: dict[str, NodeId] = Field(default_factory=dict)


class DecideNode(_NodeBase):
    """Nodo `decide` de un flow (M0 §2.5)."""
    type: Literal["decide"]
    config: DecideConfig


class RuleNode(_NodeBase):
    """Nodo `rule` de un flow (M0 §2.5)."""
    type: Literal["rule"]
    config: RuleConfig


class CollectNode(_NodeBase):
    """Nodo `collect` de un flow (M0 §2.5)."""
    type: Literal["collect"]
    config: CollectConfig


class ToolNode(_NodeBase):
    """Nodo `tool` de un flow (M0 §2.5)."""
    type: Literal["tool"]
    config: ToolConfig


class WriteToolNode(_NodeBase):
    """Nodo `write_tool` de un flow (M0 §2.5)."""
    type: Literal["tool"]
    config: WriteToolConfig


class ConfirmNode(_NodeBase):
    """Nodo `confirm` de un flow (M0 §2.5)."""
    type: Literal["confirm"]
    config: ConfirmConfig


class VerifyNode(_NodeBase):
    """Nodo `verify` de un flow (M0 §2.5)."""
    type: Literal["verify"]
    config: VerifyConfig


class RespondNode(_NodeBase):
    """Nodo `respond` de un flow (M0 §2.5)."""
    type: Literal["respond"]
    config: RespondConfig


class EscalateNode(_NodeBase):
    """Nodo `escalate` de un flow (M0 §2.5)."""
    type: Literal["escalate"]
    config: EscalateConfig


class EndNode(_NodeBase):
    """Nodo `end` de un flow (M0 §2.5)."""
    type: Literal["end"]
    config: EndConfig


class KnowledgeNode(_NodeBase):
    """Nodo `knowledge` de un flow (M0 §2.5, M12)."""
    type: Literal["knowledge"]
    config: KnowledgeConfig


class AgentNode(_NodeBase):
    """Nodo `agent` de un flow (M0 §2.5)."""
    type: Literal["agent"]
    config: AgentNodeConfig


class SubflowNode(_NodeBase):
    """Nodo `subflow` de un flow (M0 §2.5)."""
    type: Literal["subflow"]
    config: SubflowConfig


class AwaitApprovalNode(_NodeBase):
    """Nodo `await_approval` de un flow (M0 §2.5)."""
    type: Literal["await_approval"]
    config: AwaitApprovalConfig


def node_kind(value: Any) -> str | None:
    """Discriminador: `type`, salvo `tool` con `action_from` → `tool_write`."""
    if isinstance(value, dict):
        kind = value.get("type")
        config = value.get("config")
        if kind == "tool" and isinstance(config, dict) and "action_from" in config:
            return "tool_write"
        return kind if isinstance(kind, str) else None
    if isinstance(value, WriteToolNode):
        return "tool_write"
    kind_attr = getattr(value, "type", None)
    return kind_attr if isinstance(kind_attr, str) else None


Node = Annotated[
    Annotated[DecideNode, Tag("decide")]
    | Annotated[RuleNode, Tag("rule")]
    | Annotated[CollectNode, Tag("collect")]
    | Annotated[ToolNode, Tag("tool")]
    | Annotated[WriteToolNode, Tag("tool_write")]
    | Annotated[ConfirmNode, Tag("confirm")]
    | Annotated[VerifyNode, Tag("verify")]
    | Annotated[RespondNode, Tag("respond")]
    | Annotated[EscalateNode, Tag("escalate")]
    | Annotated[EndNode, Tag("end")]
    | Annotated[KnowledgeNode, Tag("knowledge")]
    | Annotated[AgentNode, Tag("agent")]
    | Annotated[SubflowNode, Tag("subflow")]
    | Annotated[AwaitApprovalNode, Tag("await_approval")],
    Discriminator(node_kind),
]

RESULTS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "decide": frozenset({"low_confidence"}),  # + valores del enum de branch_on (M1 G0-03)
        "rule": frozenset({"true", "false"}),
        "collect": frozenset({"ok", "max_attempts"}),
        "tool": frozenset({"ok", "error", "timeout", "denied"}),
        "tool_write": frozenset({"ok", "denied", "uncertain"}),
        "confirm": frozenset({"yes", "no", "unclear", "max_attempts"}),
        "verify": frozenset({"verified", "failed"}),
        "respond": frozenset({"next"}),
        "escalate": frozenset(),
        "end": frozenset(),
        # `low_confidence` es solo de `navigate`; un `read` cablea ok, not_found y denied (M1 G0-03)
        "knowledge": frozenset({"ok", "not_found", "denied", "low_confidence"}),
        "agent": frozenset({"answered", "gave_up"}),
        "subflow": frozenset(),  # los declara el subflow
        "await_approval": frozenset({"approved", "rejected", "timeout"}),
    }
)
TERMINAL: frozenset[str] = frozenset({"escalate", "end"})
WAITING: frozenset[str] = frozenset({"collect", "confirm"})  # más respond con await: true
# `agent` se habilitó con el ADR 0019 (solo lectura y cálculo); los otros dos siguen sin habilitar.
PRODUCTION_NODE_KINDS: frozenset[str] = frozenset({"subflow", "await_approval"})
MVP_NODE_KINDS: frozenset[str] = frozenset(RESULTS) - PRODUCTION_NODE_KINDS
