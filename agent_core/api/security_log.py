"""Log de seguridad sobre OpenTelemetry y `logging` (ADR 0003): campos de una lista cerrada."""

import logging

from opentelemetry import trace

log = logging.getLogger("agentcore.security")


class OtelSecurityLog:
    """Evento en el span activo (si lo hay) y una línea de log JSON-correlacionable con la traza."""

    def record(self, reason: str, principal_type: str | None, trace_id: str) -> None:
        attributes = {"agentcore.reason": reason, "agentcore.principal_type": principal_type or "unknown"}
        trace.get_current_span().add_event("agentcore.access_rejected", attributes)
        log.warning("acceso rechazado: %s (%s) trace_id=%s", reason, principal_type or "unknown", trace_id)
