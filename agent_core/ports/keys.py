from enum import StrEnum
from typing import Protocol


class KeyPurpose(StrEnum):
    """Propósito de una clave: huella o cifrado del `token_map` (M0 §2.9)."""
    fingerprint = "fingerprint"
    token_map = "token_map"


class KeyProvider(Protocol):
    """Claves distintas por propósito: nunca la misma clave para HMAC y cifrado.

    El material de clave solo sale por `key()`; ningún error ni `repr` de una implementación lo incluye."""

    def current_kid(self, purpose: KeyPurpose) -> str: ...

    def key(self, purpose: KeyPurpose, kid: str) -> bytes:
        """`kid` desconocido → `KeyError` (sin el material de ninguna clave en el mensaje)."""
        ...
