from collections.abc import Mapping
from typing import Protocol


class PublishError(Exception):
    """El bus no aceptó el mensaje. Sin detalle del proveedor: puede traer ARNs o cuentas."""


class EventPublisher(Protocol):
    """Publica un evento saliente ya serializado (`outbound`) en el bus. Al menos una vez: el que consume
    deduplica por `event_id` (spec de eventos salientes §Entrega)."""

    def publish(self, *, event_id: str, event_type: str, body: str,
                attributes: Mapping[str, str] | None = None) -> None:
        """Lanza `PublishError` si el bus no confirma."""
        ...
