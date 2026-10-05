"""Operational plane of `agentcore serve` (ADR 0003 #1/#4): traces from the standard OTEL_* variables, JSON
logs on root and flush on exit.

Configuration comes from the injected `env` (F4): the resource, sampler, endpoint and headers are validated
here and passed to the SDK explicitly. The OTLP exporter still reads the process environment on its own: it
re-parses `OTEL_EXPORTER_OTLP[_TRACES]_HEADERS` (and merges them under ours) and reads the certificates,
compression and timeout. In production `env is os.environ`, so the headers it re-parses are the ones
validated here; while the exporter is built, the SDK logger that would echo a rejected header entry is
silenced (a header may be a credential)."""

import logging
import math
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import TextIO
from urllib.parse import unquote

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import sampling
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.trace import Tracer

import agent_telemetry as tel

_TRACES_PATH = "/v1/traces"
# The SDK's header grammar (`opentelemetry.util.re`, W3C baggage plus its "liberal" spaces in values). An
# entry outside it is one the OTLP exporter would reject and log verbatim, so it is a startup problem here.
# `test_header_patterns_match_the_sdk` keeps these copies equal to the SDK's private ones.
_KEY = r"[\x21\x23-\x27\x2a\x2b\x2d\x2e\x30-\x39\x41-\x5a\x5e-\x7a\x7c\x7e]+"
_HEADER_PATTERN = re.compile(
    rf"[ \t]*{_KEY}[ \t]*=[ \t]*[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*[ \t]*")
_LIBERAL_HEADER_PATTERN = re.compile(
    rf"[ \t]*{_KEY}[ \t]*=[ \t]*[\x20\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*[ \t]*")
# SDK loggers that write header text when parsing them (`parse_env_headers` logs a rejected entry verbatim).
_HEADER_ECHOING_LOGGERS = ("opentelemetry.util.re",)
_SDK_LOGGERS = ("httpx", "httpcore")  # HTTP client loggers: DEBUG shows request details
_RATIO_SAMPLERS = ("traceidratio", "parentbased_traceidratio")
_SAMPLERS: Mapping[str, sampling.Sampler | None] = {
    "always_on": sampling.ALWAYS_ON,
    "always_off": sampling.ALWAYS_OFF,
    "traceidratio": None,  # built from OTEL_TRACES_SAMPLER_ARG
    "parentbased_always_on": sampling.ParentBased(sampling.ALWAYS_ON),
    "parentbased_always_off": sampling.ParentBased(sampling.ALWAYS_OFF),
    "parentbased_traceidratio": None,
}


class ObservabilityConfigError(Exception):
    """Invalid OTEL_* values: a startup error (exit 2). The problems name variables, never header values."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class TracingConfig:
    endpoint: str
    headers: Mapping[str, str] = field(repr=False)  # may hold credentials: never printed or logged
    resource: Mapping[str, str]
    sampler: sampling.Sampler


def tracing_config(env: Mapping[str, str], *, version: str) -> TracingConfig | None:
    """OpenTelemetry SDK environment variables (subset, F4). `None` = tracing off (no endpoint, disabled or
    `OTEL_TRACES_EXPORTER=none`)."""
    if env.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return None
    problems: list[str] = []
    exporter = (env.get("OTEL_TRACES_EXPORTER") or "otlp").strip().lower()
    if exporter == "none":
        return None
    if exporter != "otlp":
        problems.append("OTEL_TRACES_EXPORTER solo admite `otlp` o `none`")
    endpoint = env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
    if not endpoint and (base := env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()):
        endpoint = base.removesuffix("/") + _TRACES_PATH
    if not endpoint:
        if problems:
            raise ObservabilityConfigError(problems)
        return None
    # the signal-specific variable wins over the generic one (OTLP exporter specification)
    traces_protocol = env.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "").strip()
    protocol_var = "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL" if traces_protocol else "OTEL_EXPORTER_OTLP_PROTOCOL"
    if (protocol := env.get(protocol_var, "").strip()) and protocol != "http/protobuf":
        problems.append(f"{protocol_var}: solo `http/protobuf` (no hay exportador gRPC ni http/json)")
    sampler = _sampler(env.get("OTEL_TRACES_SAMPLER"), env.get("OTEL_TRACES_SAMPLER_ARG"), problems)
    headers_var = ("OTEL_EXPORTER_OTLP_TRACES_HEADERS" if env.get("OTEL_EXPORTER_OTLP_TRACES_HEADERS")
                   else "OTEL_EXPORTER_OTLP_HEADERS")
    headers = _headers(headers_var, env.get(headers_var, ""), problems)
    if problems or sampler is None:
        raise ObservabilityConfigError(problems)
    resource = {"service.name": "agentcore", "service.version": version,
                **_resource_attributes(env.get("OTEL_RESOURCE_ATTRIBUTES", ""))}
    if service := env.get("OTEL_SERVICE_NAME", "").strip():
        resource["service.name"] = service  # wins over OTEL_RESOURCE_ATTRIBUTES, as in the spec
    return TracingConfig(endpoint=endpoint, headers=headers, resource=resource, sampler=sampler)


def _sampler(name: str | None, arg: str | None, problems: list[str]) -> sampling.Sampler | None:
    """The six standard samplers; `traceidratio` takes a ratio in [0, 1] from OTEL_TRACES_SAMPLER_ARG
    (default 1.0)."""
    key = (name or "parentbased_always_on").strip().lower()
    if key not in _SAMPLERS:
        problems.append("OTEL_TRACES_SAMPLER: admite " + ", ".join(f"`{k}`" for k in _SAMPLERS))
        return None
    if key not in _RATIO_SAMPLERS:
        return _SAMPLERS[key]
    raw = (arg or "").strip()
    try:
        ratio = float(raw) if raw else 1.0
    except ValueError:
        ratio = math.nan
    if not 0.0 <= ratio <= 1.0:
        problems.append("OTEL_TRACES_SAMPLER_ARG: debe ser un número entre 0 y 1")
        return None
    base = sampling.TraceIdRatioBased(ratio)
    return sampling.ParentBased(base) if key.startswith("parentbased_") else base


def _headers(variable: str, raw: str, problems: list[str]) -> dict[str, str]:
    """`k=v` pairs separated by commas, with the SDK's grammar and decoding (URL-decoded only when the entry
    is strict W3C baggage). Not the SDK's parser: it logs a rejected entry verbatim, and an entry may be a
    credential. A problem names the variable and the position, never the text."""
    headers: dict[str, str] = {}
    for position, entry in enumerate(raw.split(","), start=1):
        if not entry.strip():
            continue
        if _LIBERAL_HEADER_PATTERN.fullmatch(entry.strip()) is None:
            problems.append(f"{variable}: la entrada {position} no es `clave=valor` válida")
            continue
        key, _, value = entry.strip().partition("=")
        if _HEADER_PATTERN.fullmatch(entry.strip()) is not None:
            key, value = unquote(key), unquote(value)
        headers[key.strip().lower()] = value.strip()
    return headers


def _resource_attributes(raw: str) -> dict[str, str]:
    """`k=v` pairs separated by commas, URL-decoded; empty or malformed entries are ignored (as the SDK
    does)."""
    attributes: dict[str, str] = {}
    for entry in raw.split(","):
        key, sep, value = entry.partition("=")
        if sep and key.strip():
            attributes[unquote(key).strip()] = unquote(value).strip()
    return attributes


def _version() -> str:
    try:
        return package_version("agent-core")
    except PackageNotFoundError:
        return "0+unknown"


@dataclass(frozen=True)
class _SavedLogging:
    handlers: tuple[logging.Handler, ...]
    levels: Mapping[str, int]  # "" is root


_SAVED: _SavedLogging | None = None  # logging as it was before the first live setup
_ACTIVE: "Observability | None" = None  # the setup whose shutdown restores it; a later setup replaces it


@dataclass(eq=False)
class Observability:
    """What `setup_observability` installed; `shutdown()` undoes it. A second setup replaces the first (like
    `setup_tracing`): the replaced one's `shutdown()` is then a no-op, and the active one's restores the
    logging saved before the first setup, so any shutdown order leaves the process as it was."""

    tracing: bool
    _handler: logging.Handler = field(repr=False)

    def tracer(self, name: str) -> Tracer:
        return tel.tracer(name)

    def shutdown(self) -> None:
        """Flush pending spans (BatchSpanProcessor, I6), then put back the root handlers and the logger levels
        saved before the first setup. Idempotent."""
        global _ACTIVE, _SAVED
        if _ACTIVE is not self:
            return
        tel.shutdown_tracing()
        tel.configure(capture_content=False, langfuse_attributes=False)
        saved, _ACTIVE, _SAVED = _SAVED, None, None
        if saved is not None:
            root = logging.getLogger()
            root.handlers[:] = list(saved.handlers)
            for name, level in saved.levels.items():
                logging.getLogger(name).setLevel(level)


def quiet_sdk_loggers() -> None:
    """HTTP client libraries log request details at DEBUG: never below WARNING."""
    for name in _SDK_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def install_json_logging(stream: TextIO, level: int = logging.INFO) -> logging.Handler:
    """Make `JsonLogFormatter` the **only** root handler (F6, I1). A foreign handler (e.g. a `basicConfig`
    run by a client library at import) would print an exception's message and stack in
    plain text next to the JSON line; the caller restores the previous handlers on shutdown."""
    root = logging.getLogger()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(tel.JsonLogFormatter())
    handler.agentcore = True  # type: ignore[attr-defined]  # marks the handler this module owns
    root.handlers[:] = [handler]
    root.setLevel(level)
    return handler


@contextmanager
def _sdk_header_logs_silenced() -> Iterator[None]:
    loggers = [logging.getLogger(name) for name in _HEADER_ECHOING_LOGGERS]
    previous = [lg.disabled for lg in loggers]
    for lg in loggers:
        lg.disabled = True
    try:
        yield
    finally:
        for lg, value in zip(loggers, previous, strict=True):
            lg.disabled = value


def setup_observability(env: Mapping[str, str], *, version: str | None = None,
                        exporter: SpanExporter | None = None, stream: TextIO | None = None) -> Observability:
    """Traces (when OTEL_* name an endpoint, or with an explicit `exporter` in tests), JSON logs as the only
    root handler at INFO and the SDK loggers at WARNING. Raises `ObservabilityConfigError` before touching
    anything."""
    global _ACTIVE, _SAVED
    config = tracing_config(env, version=version or _version())
    if config is not None or exporter is not None:  # first: if it raises, logging is still untouched
        with _sdk_header_logs_silenced():  # the exporter re-parses the process's header variables (C1)
            tel.setup_tracing(
                endpoint=config.endpoint if config is not None else None, exporter=exporter,
                headers=config.headers if config is not None else None,
                resource=Resource(dict(config.resource)) if config is not None else None,
                sampler=config.sampler if config is not None else None)
    if _SAVED is None:
        root = logging.getLogger()
        _SAVED = _SavedLogging(handlers=tuple(root.handlers),
                               levels={name: logging.getLogger(name).level for name in ("", *_SDK_LOGGERS)})
    # Env-only switches (default off): `AGENTCORE_TRACE_CONTENT=1` admits prompt/completion attributes on
    # spans (the `audit` view, rule 6); `AGENTCORE_TRACE_LANGFUSE=1` derives the `langfuse.*` attributes.
    tel.configure(capture_content=env.get("AGENTCORE_TRACE_CONTENT", "").strip() == "1",
                  langfuse_attributes=env.get("AGENTCORE_TRACE_LANGFUSE", "").strip() == "1")
    handler = install_json_logging(stream or sys.stderr)
    quiet_sdk_loggers()
    _ACTIVE = Observability(tracing=config is not None or exporter is not None, _handler=handler)
    return _ACTIVE
