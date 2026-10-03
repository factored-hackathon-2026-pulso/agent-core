"""Blobs del registry en S3 con el registry en Postgres real: lo nuevo va a S3, lo anterior se sigue
leyendo de `reg_blobs`, y el backfill termina la migración."""

from typing import Any

import boto3
import psycopg
import pytest
from moto import mock_aws

from agent_core.registry.postgres.store import (
    PgRegistryStore,
    detach_blob_foreign_key,
    pg_blob_rows,
    read_through,
)
from agent_core.registry.s3_blobs import S3BlobStore, backfill
from agent_core.registry.service import RegistryService
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.registry.helpers import REGISTRY_DEMO, admin
from tests.registry.service_world import FakeEvaluator

pytestmark = pytest.mark.integration
BUCKET = "agentcore-it-blobs"


def _seed(store: PgRegistryStore) -> None:
    RegistryService(store, FakeEvaluator(), FakeClock(), FakeIds()).import_seed(admin(), REGISTRY_DEMO)


def _hashes(admin_conn: "psycopg.Connection[Any]") -> list[str]:
    return [str(r[0]).strip() for r in admin_conn.execute("SELECT hash FROM reg_blobs ORDER BY hash")]


def test_new_blobs_go_to_s3_and_not_to_reg_blobs(registry_store: PgRegistryStore,
                                                 admin_conn: "psycopg.Connection[Any]") -> None:
    detach_blob_foreign_key(admin_conn)
    detach_blob_foreign_key(admin_conn)  # idempotente
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        s3 = S3BlobStore(client, BUCKET)
        store = PgRegistryStore(registry_store.connect, read_through(s3))

        _seed(store)

        assert _hashes(admin_conn) == []
        keys = [o["Key"] for o in client.list_objects_v2(Bucket=BUCKET)["Contents"]]
        assert keys and all(k.startswith("blobs/") for k in keys)


def test_blobs_written_before_the_migration_stay_readable_and_backfill_moves_them(
        registry_store: PgRegistryStore, admin_conn: "psycopg.Connection[Any]") -> None:
    _seed(registry_store)  # sin S3: todo en reg_blobs
    legacy = _hashes(admin_conn)
    assert legacy

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        s3 = S3BlobStore(client, BUCKET)
        store = PgRegistryStore(registry_store.connect, read_through(s3))

        with store.transaction() as tx:
            assert tx.blobs.get(legacy[0])  # fallback a Postgres

        with registry_store.connect() as conn:
            copied, present = backfill(pg_blob_rows(conn), s3)
        assert (copied, present) == (len(legacy), 0)
        assert all(s3.contains(h) for h in legacy)
        with registry_store.connect() as conn:
            assert backfill(pg_blob_rows(conn), s3) == (0, len(legacy))
