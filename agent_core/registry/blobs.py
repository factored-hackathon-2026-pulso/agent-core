"""`BlobStore`: contenido direccionado por hash (spec §2 #2). Toda lectura verifica el hash."""

from typing import Protocol

from agent_core.domain import sha256_hex
from agent_core.registry.errors import IntegrityError


class BlobStore(Protocol):
    def put(self, data: bytes) -> str: ...

    def get(self, digest: str) -> bytes: ...


def verified(digest: str, data: bytes) -> bytes:
    if sha256_hex(data) != digest:
        raise IntegrityError(f"el contenido {digest[:12]}… no coincide con su hash")
    return data


class InMemoryBlobStore:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, data: bytes) -> str:
        digest = sha256_hex(data)
        self._data.setdefault(digest, bytes(data))
        return digest

    def get(self, digest: str) -> bytes:
        try:
            return verified(digest, self._data[digest])
        except KeyError:
            raise IntegrityError(f"no existe el contenido {digest[:12]}…") from None

    def corrupt(self, digest: str, data: bytes) -> None:
        """Solo para pruebas (T-REG-19)."""
        self._data[digest] = data
