"""Único lugar del repo que usa aleatoriedad: UUIDv7 para IDs y `secrets` para tokens (M0 §3)."""

import secrets
import time
import uuid
from typing import TYPE_CHECKING

from agent_core.ports.ids import IdKind

_SECRET_TOKEN_BYTES = 16  # 128 bits: mínimo del contrato de IdSource.secret_token


def _uuid7() -> uuid.UUID:
    ms = time.time_ns() // 1_000_000
    rand = secrets.token_bytes(10)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= (int.from_bytes(rand[:2], "big") & 0x0FFF) << 64
    value |= 0b10 << 62
    value |= int.from_bytes(rand[2:], "big") & ((1 << 62) - 1)
    return uuid.UUID(int=value)


class SystemIds:
    """Sin estado: no hay nada que filtrar por `repr`. El token nunca se guarda ni se registra aquí."""

    def new_id(self, kind: IdKind) -> str:
        return str(_uuid7())

    def secret_token(self) -> str:
        return secrets.token_urlsafe(_SECRET_TOKEN_BYTES)


if TYPE_CHECKING:
    from agent_core.ports.ids import IdSource

    def _conforms(x: SystemIds) -> IdSource:
        return x
