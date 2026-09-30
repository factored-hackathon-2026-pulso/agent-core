"""Puertos propios de M9: lo que la API necesita de M4, M10 y M11, y del log de seguridad.

Son `Protocol` estructurales: `TurnEngine`, `HandoffService`, `AuditLog` y `TranscriptReader` los cumplen sin
que `agent_core.api` importe sus módulos (así el contrato `api` de `.importlinter` no arrastra imports
indirectos). El cableado (`agent_core.composition`) es quien los conecta."""

from collections.abc import Sequence
from typing import Protocol

from agent_core.domain import EngineEvent


class DenialRecorder(Protocol):
    """`AuditLog.append_standalone` (M11): añade eventos a la cadena de un run fuera de un turno."""

    def append_standalone(self, run_id: str, events: list[EngineEvent]) -> Sequence[object]: ...


class SecurityLog(Protocol):
    """Registro de seguridad: rechazos de la puerta, con o sin run. Nunca recibe la credencial, el
    `principal.id` ni el cuerpo; solo el motivo, el tipo de principal (si se llegó a verificar) y el trace."""

    def record(self, reason: str, principal_type: str | None, trace_id: str) -> None: ...
