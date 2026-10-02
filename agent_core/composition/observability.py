"""Operational plane of `agentcore serve` (ADR 0003 #1/#4): traces from the standard OTEL_* variables, JSON
logs on root and flush on exit. Reads only the injected `env` (F4): the resource, sampler, endpoint and
headers are passed to the SDK explicitly. Exception: the exporter's certificates, compression and timeout are
still read by the SDK from `os.environ` (in production `env is os.environ`)."""

import logging
import math
import sys
from collections.abc import Mapping
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
_SDK_LOGGERS = ("openai", "httpx", "httpcore")  # the openai SDK logs request bodies (model view) at DEBUG
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
    for name in ("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL"):
        if (protocol := env.get(name, "").strip()) and protocol != "http/protobuf":
            problems.append(f"{name}: solo `http/protobuf` (no hay exportador gRPC ni http/json)")
            break
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
    """`k=v` pairs separated by commas, URL-decoded (W3C baggage format). Not the SDK's parser: it logs a
    malformed entry verbatim, and an entry may be a credential. A problem names the position, never the
    text."""
    headers: dict[str, str] = {}
    for position, entry in enumerate(raw.split(","), start=1):
        if not entry.strip():
            continue
        key, sep, value = entry.partition("=")
        if not sep or not key.strip():
            problems.append(f"{variable}: la entrada {position} no es `clave=valor`")
            continue
        headers[unquote(key).strip().lower()] = unquote(value).strip()
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


@dataclass
class Observability:
    """What `setup_observability` installed; `shutdown()` undoes it."""

    tracing: bool
    _handler: logging.Handler = field(repr=False)
    _levels: Mapping[str, int] = field(repr=False, default_factory=dict)

    def tracer(self, name: str) -> Tracer:
        return tel.tracer(name)

    def shutdown(self) -> None:
        """Flush pending spans (BatchSpanProcessor, I6), remove the root handler and restore the logger levels
        it changed. Idempotent."""
        tel.shutdown_tracing()
        root = logging.getLogger()
        if self._handler in root.handlers:
            root.removeHandler(self._handler)
            for name, level in self._levels.items():
                logging.getLogger(name).setLevel(level)


def quiet_sdk_loggers() -> None:
    """The openai SDK logs request bodies at DEBUG (even with OPENAI_LOG=debug): never below WARNING."""
    for name in _SDK_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def install_json_logging(stream: TextIO, level: int = logging.INFO) -> logging.Handler:
    """The only agentcore handler on root (replaces a previous one), with `JsonLogFormatter` (F6)."""
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, "agentcore", False)]:
        root.removeHandler(old)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(tel.JsonLogFormatter())
    handler.agentcore = True  # type: ignore[attr-defined]  # marks the handler this module owns
    root.addHandler(handler)
    root.setLevel(level)
    return handler


def setup_observability(env: Mapping[str, str], *, version: str | None = None,
                        exporter: SpanExporter | None = None, stream: TextIO | None = None) -> Observability:
    """Traces (when OTEL_* name an endpoint, or with an explicit `exporter` in tests), JSON logs on root at
    INFO and the SDK loggers at WARNING. Raises `ObservabilityConfigError` before touching anything."""
    config = tracing_config(env, version=version or _version())
    levels = {name: logging.getLogger(name).level for name in ("", *_SDK_LOGGERS)}
    if config is not None or exporter is not None:
        tel.setup_tracing(
            endpoint=config.endpoint if config is not None else None, exporter=exporter,
            headers=config.headers if config is not None else None,
            resource=Resource(dict(config.resource)) if config is not None else None,
            sampler=config.sampler if config is not None else None)
    handler = install_json_logging(stream or sys.stderr)
    quiet_sdk_loggers()
    return Observability(tracing=config is not None or exporter is not None, _handler=handler, _levels=levels)
