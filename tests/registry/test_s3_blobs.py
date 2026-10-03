"""`S3BlobStore` contra S3 simulado (moto): mismo contrato que `InMemoryBlobStore` más lo propio de S3."""

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from agent_core.domain import sha256_hex
from agent_core.registry.blobs import InMemoryBlobStore
from agent_core.registry.errors import IntegrityError
from agent_core.registry.s3_blobs import ReadThroughBlobStore, S3BlobStore, backfill

BUCKET = "agentcore-test-blobs"


@pytest.fixture
def s3() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        yield client


def test_put_returns_the_sha256_and_get_roundtrips(s3: Any) -> None:
    store = S3BlobStore(s3, BUCKET)

    digest = store.put(b"hola")

    assert digest == sha256_hex(b"hola")
    assert store.get(digest) == b"hola"
    assert s3.list_objects_v2(Bucket=BUCKET)["Contents"][0]["Key"] == f"blobs/{digest}"


def test_putting_the_same_content_twice_writes_one_version(s3: Any) -> None:
    store = S3BlobStore(s3, BUCKET)

    store.put(b"igual")
    store.put(b"igual")

    versions = s3.list_object_versions(Bucket=BUCKET)["Versions"]
    assert len(versions) == 1


def test_a_missing_blob_is_an_integrity_error(s3: Any) -> None:
    with pytest.raises(IntegrityError, match="no existe"):
        S3BlobStore(s3, BUCKET).get(sha256_hex(b"nunca escrito"))


def test_a_tampered_object_is_detected_on_read(s3: Any) -> None:
    store = S3BlobStore(s3, BUCKET)
    digest = store.put(b"original")
    s3.put_object(Bucket=BUCKET, Key=f"blobs/{digest}", Body=b"alterado")

    with pytest.raises(IntegrityError, match="no coincide"):
        store.get(digest)


def test_other_s3_errors_are_not_disguised_as_missing(s3: Any) -> None:
    store = S3BlobStore(s3, "bucket-que-no-existe")

    with pytest.raises(Exception) as info:
        store.put(b"x")
    assert not isinstance(info.value, IntegrityError)


def test_read_through_writes_to_primary_only_and_falls_back_for_old_blobs(s3: Any) -> None:
    primary, old = S3BlobStore(s3, BUCKET), InMemoryBlobStore()
    legacy = old.put(b"anterior a la migracion")
    store = ReadThroughBlobStore(primary, old)

    fresh = store.put(b"nuevo")

    assert store.get(fresh) == b"nuevo" and not primary.contains(legacy)
    assert store.get(legacy) == b"anterior a la migracion"
    with pytest.raises(IntegrityError):
        store.get(sha256_hex(b"en ninguno"))


def test_backfill_copies_missing_blobs_and_is_idempotent(s3: Any) -> None:
    target = S3BlobStore(s3, BUCKET)
    rows = [(sha256_hex(b"a"), b"a"), (sha256_hex(b"b"), b"b")]

    assert backfill(iter(rows), target) == (2, 0)
    assert backfill(iter(rows), target) == (0, 2)
    assert target.get(sha256_hex(b"b")) == b"b"


def test_backfill_rejects_a_corrupt_row(s3: Any) -> None:
    with pytest.raises(IntegrityError):
        backfill(iter([(sha256_hex(b"a"), b"corrupto")]), S3BlobStore(s3, BUCKET))
