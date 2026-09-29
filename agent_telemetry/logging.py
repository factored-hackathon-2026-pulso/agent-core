"""Logs JSON correlacionados con la traza (ADR 0003 #4)."""

import json
import logging

from opentelemetry import trace

from agent_telemetry.context import current


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = trace.get_current_span().get_span_context()
        body = {"level": record.levelname, "logger": record.name, "message": record.getMessage(), **current()}
        if ctx.is_valid:
            body["trace_id"] = format(ctx.trace_id, "032x")
        return json.dumps(body, ensure_ascii=False)
