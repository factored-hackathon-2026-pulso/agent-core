"""`UnderstandService`: un `DecisionModel` más, con el esquema cerrado que arma cada release (spec §3.2).

Forma de `UnderstandContext` y `UnderstandResult` fijada con el usuario (P3). `slots` salen como `claimed`:
`UnderstandResult` no fabrica `Slot` (eso es de M4/M2); el valor se entrega tal cual, **sin validar**, y
M4/M2 no deben tratarlo como hecho."""

from dataclasses import dataclass, field

from agent_core.decision.service import DecisionService
from agent_core.decision.types import EventScope
from agent_core.domain import Command, DecisionMade, EntityRef, JsonValue, Locale
from agent_core.views import TokenVault


@dataclass(frozen=True)
class UnderstandContext:
    model_ref: EntityRef                 # de Agent.understand (RefSpec resuelto por M4)
    flows: list[str]                     # enum de flows de la release
    interrupts: list[str]                # enum de interrupciones de la release
    current_node: str | None
    confirm_pending: bool
    recent_turns: list[str]              # `text_model` de TranscriptEntry (vista model); M4 arma n fijo
    token_vault: TokenVault
    scope: EventScope


@dataclass(frozen=True)
class UnderstandResult:
    """Salida de Understand. Solo llevan marca los campos calibrados relevantes (`command`; `flow` con
    `start_flow`; `interrupt` con `interrupt`). `additional_flows` y `slots` nunca llevan umbral."""
    command: Command
    flow: str | None
    interrupt: str | None
    additional_flows: list[str]
    slots: dict[str, JsonValue]          # claimed: sin validar
    above_threshold: dict[str, bool]
    decision_id: str
    p_cal: dict[str, float | None] = field(default_factory=dict)

    def __repr__(self) -> str:
        # Sin `slots`: pueden llevar contenido del usuario (tokens).
        return (f"UnderstandResult(command={self.command.value!r}, flow={self.flow!r}, "
                f"interrupt={self.interrupt!r}, above_threshold={self.above_threshold!r}, "
                f"decision_id={self.decision_id!r})")


def _string_enum(values: list[str]) -> dict[str, JsonValue]:
    return {"type": "string", "enum": list(values)}


def _schema(flows: list[str], interrupts: list[str]) -> dict[str, JsonValue]:
    """Esquema cerrado de la release; `slots` es el único objeto libre."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["command"],
        "properties": {
            "command": _string_enum([command.value for command in Command]),
            "flow": _string_enum(flows),
            "interrupt": _string_enum(interrupts),
            "additional_flows": {"type": "array", "items": _string_enum(flows)},
            "slots": {"type": "object", "additionalProperties": True},
        },
    }


class UnderstandService:
    def __init__(self, decisions: DecisionService) -> None:
        self._decisions = decisions

    def run(self, model_view_text: str, context: UnderstandContext, locale: Locale
            ) -> tuple[UnderstandResult, list[DecisionMade]]:
        inputs: dict[str, JsonValue] = {
            "text": model_view_text, "recent_turns": list(context.recent_turns),
            "current_node": context.current_node, "confirm_pending": context.confirm_pending,
        }
        output, event = self._decisions.decide_with_schema(
            context.model_ref, _schema(context.flows, context.interrupts), inputs, locale,
            context.token_vault, scope=context.scope)
        value = output.value
        raw_command = value.get("command")  # ausente con la cadena agotada: `clarify` neutro (P3)
        command = Command(raw_command) if isinstance(raw_command, str) else Command.clarify
        relevant = {"command"}
        if command is Command.start_flow:
            relevant.add("flow")
        if command is Command.interrupt:
            relevant.add("interrupt")
        flow, interrupt = value.get("flow"), value.get("interrupt")
        additional, slots = value.get("additional_flows"), value.get("slots")
        result = UnderstandResult(
            command=command,
            flow=flow if isinstance(flow, str) else None,
            interrupt=interrupt if isinstance(interrupt, str) else None,
            additional_flows=([f for f in additional if isinstance(f, str)]
                              if isinstance(additional, list) else []),
            slots=slots if isinstance(slots, dict) else {},
            above_threshold={k: v for k, v in output.above_threshold.items() if k in relevant},
            decision_id=output.decision_id,
            p_cal={k: v for k, v in output.p_cal.items() if k in relevant},
        )
        return result, [event]
