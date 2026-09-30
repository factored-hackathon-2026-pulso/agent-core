"""Puertos propios de M9: lo que la API necesita de M4, M10 y M11, y del log de seguridad.

Son `Protocol` estructurales: `TurnEngine`, `HandoffService`, `AuditLog` y `TranscriptReader` los cumplen sin
que `agent_core.api` importe sus módulos (así el contrato `api` de `.importlinter` no arrastra imports
indirectos). El cableado (`agent_core.composition`) es quien los conecta."""

from collections.abc import Sequence
from typing import Literal, Protocol

from agent_core.domain import (
    EngineEvent,
    JsonValue,
    OnBehalfOf,
    Principal,
    RunInput,
    RunResult,
    TurnInput,
    TurnResult,
)

# Mismo vocabulario que `handoff.HandoffQuality` (M10); se repite para no importar el módulo.
HandoffQuality = Literal["useful", "incomplete", "unnecessary"]


class DenialRecorder(Protocol):
    """`AuditLog.append_standalone` (M11): añade eventos a la cadena de un run fuera de un turno."""

    def append_standalone(self, run_id: str, events: list[EngineEvent]) -> Sequence[object]: ...


class SecurityLog(Protocol):
    """Registro de seguridad: rechazos de la puerta, con o sin run. Nunca recibe la credencial, el
    `principal.id` ni el cuerpo; solo el motivo, el tipo de principal (si se llegó a verificar) y el trace."""

    def record(self, reason: str, principal_type: str | None, trace_id: str) -> None: ...


class TurnService(Protocol):
    """M4 (`TurnEngine`)."""

    def start_run(
        self, principal: Principal, on_behalf_of: OnBehalfOf | None, run_input: RunInput
    ) -> RunResult: ...

    def handle_turn(
        self, principal: Principal, on_behalf_of: OnBehalfOf | None, turn: TurnInput
    ) -> TurnResult: ...


class HandoffReader(Protocol):
    """M10 (`HandoffService`)."""

    def get(
        self, handoff_ref: str, reader: Principal, on_behalf_of: OnBehalfOf | None = None
    ) -> dict[str, JsonValue]: ...

    def record_resolution(
        self,
        handoff_ref: str,
        reader: Principal,
        resolution_code: str,
        handoff_quality: HandoffQuality,
        notes: str | None = None,
        *,
        on_behalf_of: OnBehalfOf | None = None,
    ) -> object: ...


class RenderedEntryView(Protocol):
    """Lo que la API publica de cada entrada de `audit.RenderedEntry`."""

    @property
    def turn_id(self) -> str: ...
    @property
    def role(self) -> str: ...
    @property
    def text(self) -> str: ...
    @property
    def reason(self) -> str | None: ...
    @property
    def unknown_tokens(self) -> Sequence[str]: ...


class TranscriptService(Protocol):
    """M11 (`TranscriptReader`). Un run inexistente lanza `LookupError` (`RunNotFound` lo es)."""

    def read_rendered(
        self, run_id: str, reader: Principal, on_behalf_of: OnBehalfOf | None
    ) -> Sequence[RenderedEntryView]: ...
