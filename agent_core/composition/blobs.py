"""Blobs del registry en S3 (ADR 0023): configuración por entorno y `agentcore blobs-backfill`.

Sin `AGENTCORE_BLOB_BUCKET` el registry sigue guardando los blobs en `reg_blobs` (Postgres)."""

import argparse
import sys
from collections.abc import Mapping
from typing import Any

import boto3
import psycopg

from agent_core.registry import S3BlobStore
from agent_core.registry.postgres.store import BlobFactory, pg_blob_rows, read_through
from agent_core.registry.s3_blobs import backfill

BUCKET_ENV = "AGENTCORE_BLOB_BUCKET"
PREFIX_ENV = "AGENTCORE_BLOB_PREFIX"
KMS_ENV = "AGENTCORE_BLOB_KMS_KEY_ARN"
DSN_ENV = "AGENTCORE_REGISTRY_DSN"


def s3_blob_store_from_env(env: Mapping[str, str]) -> S3BlobStore | None:
    """`None` si no hay bucket configurado. Región y credenciales las resuelve `boto3` (rol de la tarea)."""
    bucket = env.get(BUCKET_ENV, "").strip()
    if not bucket:
        return None
    return S3BlobStore(boto3.client("s3"), bucket, prefix=env.get(PREFIX_ENV, "blobs"),
                       kms_key_arn=env.get(KMS_ENV) or None)


def blob_factory_from_env(env: Mapping[str, str]) -> BlobFactory | None:
    store = s3_blob_store_from_env(env)
    return None if store is None else read_through(store)


def add_blobs_backfill_parser(sub: Any) -> None:
    p = sub.add_parser("blobs-backfill",
                       help="copia a S3 los blobs de `reg_blobs` que aún no están (idempotente)")
    p.add_argument("--dsn", default=None, help=f"DSN de Postgres (o {DSN_ENV}); no se imprime nunca")


def run_blobs_backfill(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    dsn = args.dsn or env.get(DSN_ENV)
    target = s3_blob_store_from_env(env)
    if not dsn or target is None:
        print(f"agentcore blobs-backfill necesita --dsn (o {DSN_ENV}) y {BUCKET_ENV}", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(dsn) as conn:  # transacción abierta: el cursor con nombre la necesita
            copied, present = backfill(pg_blob_rows(conn), target)
    except psycopg.Error as error:  # el mensaje de psycopg puede traer el host o la contraseña
        print(f"blobs-backfill: falla de Postgres ({type(error).__name__})", file=sys.stderr)
        return 1
    print(f"copied={copied} already_present={present}")
    return 0
