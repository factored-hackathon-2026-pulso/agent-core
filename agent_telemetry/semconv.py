"""Pinned GenAI semantic conventions (ADR 0003 #1; M11 decision 15).

Verified against `opentelemetry-semantic-conventions` 0.66b0 (uv.lock): its `Schemas` go up to 1.44.0 and
its `gen_ai_attributes` carry `gen_ai.operation.name` with the `invoke_agent`, `execute_tool` and `chat`
values these spans use. Its own module so that `setup` and `spans` can both import it without a cycle."""

SEMCONV_VERSION = "1.44.0"
SCHEMA_URL = f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}"
