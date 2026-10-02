"""JSON logs correlated with the trace (ADR 0003 #4, F6)."""

import json
import logging
import re
from datetime import UTC, datetime

from agent_telemetry.context import current
from agent_telemetry.spans import current_trace_id

# `scheme://user:password@host`: SDK warnings (e.g. OTLP export retries) may name an endpoint with
# credentials.
_URL_USERINFO = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s@]+@")


class JsonLogFormatter(logging.Formatter):
    """Closed set of fields (F6): `timestamp`, `level`, `logger`, `message`, `exc_type` (only with
    `exc_info`), the bound correlation fields and `trace_id`. Never `exc_text`, the stack, `stack_info` or
    `extra` fields: an exception's message or a payload may carry secrets or PII (rule 6). Credentials
    embedded in a URL of the message are replaced by `***`."""

    def format(self, record: logging.LogRecord) -> str:
        # `record.created` is set by `logging`: it decides nothing and never reaches an event (not our Clock).
        stamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds")
        message = _URL_USERINFO.sub(r"\1***@", record.getMessage())
        body: dict[str, object] = {"timestamp": stamp.replace("+00:00", "Z"), "level": record.levelname,
                                   "logger": record.name, "message": message, **current()}
        if record.exc_info and record.exc_info[0] is not None:
            body["exc_type"] = record.exc_info[0].__name__
        trace_id = current_trace_id()
        if trace_id is not None:
            body["trace_id"] = trace_id
        return json.dumps(body, ensure_ascii=False, default=str)
