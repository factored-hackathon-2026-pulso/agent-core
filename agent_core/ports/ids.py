from enum import StrEnum
from typing import Protocol


class IdKind(StrEnum):
    """Clases de identificador que emite el `IdSource` (M0 §2.9)."""
    run = "run"
    session = "session"
    turn = "turn"
    action = "action"
    decision = "decision"
    fact = "fact"
    call = "call"
    handoff = "handoff"
    event = "event"
    message = "message"
    proposal = "proposal"
    eval_run = "eval_run"
    transfer = "transfer"


class IdSource(Protocol):
    """Única fuente de IDs y secretos del motor (M0 §2.9)."""

    def new_id(self, kind: IdKind) -> str:
        """ID opaco y único por `kind`. Ningún módulo genera IDs por su cuenta."""
        ...

    def secret_token(self) -> str:
        """Base64 url-safe sin relleno de al menos 16 bytes (128 bits) de un CSPRNG. Solo tokens de
        confirmación. Quien lo recibe no lo registra en logs, eventos ni `repr`."""
        ...
