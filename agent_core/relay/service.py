"""`OutboxRelay.run_once`: pendientes -> proyección pública -> publicar -> marcar entregado.

Se marca entregado solo después de que el bus confirma; un corte entre ambos pasos duplica el mensaje, que el
consumidor descarta por `event_id`. Un mensaje que falla se deja pendiente y no frena a los demás de la
pasada; el contador `failed` es la señal para alarmar (uno que nunca se puede proyectar se queda al frente
de la cola: ver ADR 0023, riesgos)."""

from dataclasses import dataclass

from pydantic import ValidationError

from agent_core.domain import OutboxMessage
from agent_core.outbound import SPEC_VERSION, project_outbox_message
from agent_core.ports import EventPublisher, Outbox, PublishError


@dataclass(frozen=True)
class RelayReport:
    published: int
    failed: int
    unknown: int  # tipos de mensaje que este relay no sabe proyectar (versión nueva del motor)


class OutboxRelay:
    def __init__(self, outbox: Outbox, publisher: EventPublisher, *, batch: int = 100) -> None:
        if batch < 1:
            raise ValueError("batch debe ser positivo")
        self._outbox = outbox
        self._publisher = publisher
        self._batch = batch

    def run_once(self) -> RelayReport:
        """Vacía lo pendiente en lotes; termina cuando un lote no entrega nada nuevo."""
        published = failed = unknown = 0
        while True:
            pending = self._outbox.pending(self._batch)
            progress = 0
            failed_now = unknown_now = 0
            for message in pending:
                outcome = self._relay(message)
                if outcome == "ok":
                    progress += 1
                elif outcome == "failed":
                    failed_now += 1
                else:
                    unknown_now += 1
            published += progress
            failed, unknown = failed_now, unknown_now  # lo que sigue pendiente tras la última pasada
            if progress == 0 or len(pending) < self._batch:
                return RelayReport(published=published, failed=failed, unknown=unknown)

    def _relay(self, message: OutboxMessage) -> str:
        if message.type != "handoff_created":
            return "unknown"
        try:
            event = project_outbox_message(message)
            self._publisher.publish(
                event_id=event.event_id, event_type=event.type, body=event.model_dump_json(),
                attributes={"source": event.source, "spec_version": str(SPEC_VERSION)})
            self._outbox.mark_delivered(message.message_id)
        except (PublishError, ValidationError):
            return "failed"
        return "ok"
