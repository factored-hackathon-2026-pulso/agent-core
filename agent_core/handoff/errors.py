"""Errores propios de M10. Los de cara al usuario (403, 404, 409) son `EngineError` de M0."""

from agent_core.domain import DomainError


class HandoffPreconditionError(DomainError):
    """`escalate` recibió un run que no puede escalarse: no está `open` o conserva acciones sin invalidar.

    Es un bug de quien llama (M4), no un error de usuario."""
