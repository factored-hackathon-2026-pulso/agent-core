"""Relay del outbox (ADR 0023): lee los mensajes pendientes, los proyecta al contrato de eventos salientes
y los publica en el bus. Al menos una vez; no decide transporte (lo pone el `EventPublisher`)."""

from agent_core.relay.service import OutboxRelay, RelayReport

__all__ = ["OutboxRelay", "RelayReport"]
