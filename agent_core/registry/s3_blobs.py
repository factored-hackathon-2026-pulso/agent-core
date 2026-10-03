"""`BlobStore` sobre S3 (ADR 0023): contenido direccionado por hash, escrito una vez y verificado al leer.

La clave es `<prefijo>/<sha256>`: dos escritores del mismo contenido escriben lo mismo, así que no hay carrera
que resolver. Un blob escrito por una transacción que luego revierte queda huérfano e inofensivo (nada lo
referencia); no se borra. El cliente `boto3` se inyecta: las pruebas usan `moto`."""

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from agent_core.domain import sha256_hex
from agent_core.registry.blobs import BlobStore, verified
from agent_core.registry.errors import IntegrityError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

_MISSING = {"404", "NoSuchKey", "NotFound"}


class S3BlobStore:
    def __init__(self, client: "S3Client", bucket: str, *, prefix: str = "blobs",
                 kms_key_arn: str | None = None) -> None:
        self._s3 = client
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._kms = kms_key_arn

    def _key(self, digest: str) -> str:
        return f"{self._prefix}/{digest}" if self._prefix else digest

    def _exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _MISSING:
                return False
            raise
        return True

    def put(self, data: bytes) -> str:
        digest = sha256_hex(data)
        key = self._key(digest)
        if self._exists(key):  # mismo hash = mismo contenido: no se reescribe (el versionado crearía otra)
            return digest
        extra: dict[str, Any] = {}
        if self._kms:
            extra = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self._kms}
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType="application/octet-stream",
                            **extra)
        return digest

    def get(self, digest: str) -> bytes:
        try:
            body = self._s3.get_object(Bucket=self._bucket, Key=self._key(digest))["Body"].read()
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _MISSING:
                raise IntegrityError(f"no existe el contenido {digest[:12]}…") from None
            raise
        return verified(digest, body)

    def contains(self, digest: str) -> bool:
        return self._exists(self._key(digest))


class ReadThroughBlobStore:
    """Escribe solo en `primary` (S3) y lee de `primary`, con `fallback` (Postgres) para los blobs anteriores
    a la migración. Cuando el backfill termina, el fallback deja de usarse y se puede quitar."""

    def __init__(self, primary: BlobStore, fallback: BlobStore) -> None:
        self._primary = primary
        self._fallback = fallback

    def put(self, data: bytes) -> str:
        return self._primary.put(data)

    def get(self, digest: str) -> bytes:
        try:
            return self._primary.get(digest)
        except IntegrityError:
            return self._fallback.get(digest)  # si tampoco está aquí, lanza su propio IntegrityError


def backfill(rows: Iterator[tuple[str, bytes]], target: S3BlobStore) -> tuple[int, int]:
    """Copia a S3 los blobs `(hash, bytes)` que aún no están; devuelve `(copiados, ya_estaban)`. Verifica cada
    hash antes de subir: un blob corrupto en Postgres se rechaza con `IntegrityError`, no se propaga."""
    copied = present = 0
    for digest, data in rows:
        verified(digest, data)
        if target.contains(digest):
            present += 1
            continue
        target.put(data)
        copied += 1
    return copied, present
