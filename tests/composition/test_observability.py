"""`setup_observability` (U2, I3, I5, I6, M5): standard OTEL_* variables, JSON logs on root, flush on exit
(T-M11-14)."""

import argparse
import io
import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import sampling
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent_telemetry as tel
from agent_core.composition import serve as serve_module
from agent_core.composition.observability import (
    ObservabilityConfigError,
    setup_observability,
    tracing_config,
)
from agent_telemetry import setup as tel_setup
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer
from tests.composition.test_serve_app import make_ports

pytest_plugins = ["tests.support.otel"]


def _handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if getattr(h, "agentcore", False)]


def test_no_endpoint_means_no_tracing() -> None:
    assert tracing_config({}, version="1") is None
    assert tracing_config({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:4318", "OTEL_SDK_DISABLED": "true"},
                          version="1") is None
    assert tracing_config({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:4318", "OTEL_TRACES_EXPORTER": "none"},
                          version="1") is None


def test_standard_variables_build_endpoint_resource_headers_and_sampler() -> None:
    config = tracing_config({
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://phoenix:6006/",
        "OTEL_EXPORTER_OTLP_HEADERS": "authorization=Bearer%20k",
        "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment.name=demo,service.name=ignorado",
        "OTEL_SERVICE_NAME": "agentcore-demo",
        "OTEL_TRACES_SAMPLER": "parentbased_traceidratio", "OTEL_TRACES_SAMPLER_ARG": "0.25",
    }, version="0.1.0")
    assert config is not None
    assert config.endpoint == "http://phoenix:6006/v1/traces"
    assert config.headers == {"authorization": "Bearer k"}
    assert config.resource == {"service.name": "agentcore-demo", "service.version": "0.1.0",
                               "deployment.environment.name": "demo"}
    assert isinstance(config.sampler, sampling.ParentBased)
    assert "0.25" in config.sampler.get_description()
    assert "Bearer" not in repr(config)  # headers may hold credentials


def test_defaults_are_the_agentcore_service_and_parent_based_always_on() -> None:
    config = tracing_config({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:1"}, version="9")
    assert config is not None
    assert config.resource == {"service.name": "agentcore", "service.version": "9"}
    assert config.sampler.get_description() == sampling.ParentBased(sampling.ALWAYS_ON).get_description()
    assert config.headers == {}


@pytest.mark.parametrize(("name", "expected"), [
    ("always_on", sampling.ALWAYS_ON), ("always_off", sampling.ALWAYS_OFF),
    ("traceidratio", sampling.TraceIdRatioBased(0.5)),
    ("parentbased_always_on", sampling.ParentBased(sampling.ALWAYS_ON)),
    ("parentbased_always_off", sampling.ParentBased(sampling.ALWAYS_OFF)),
    ("parentbased_traceidratio", sampling.ParentBased(sampling.TraceIdRatioBased(0.5))),
])
def test_the_six_standard_samplers(name: str, expected: sampling.Sampler) -> None:
    config = tracing_config({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:1", "OTEL_TRACES_SAMPLER": name,
                             "OTEL_TRACES_SAMPLER_ARG": "0.5"}, version="1")
    assert config is not None and config.sampler.get_description() == expected.get_description()


def test_the_traces_specific_endpoint_wins_and_is_used_verbatim() -> None:
    config = tracing_config({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://a:1",
                             "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://b:2/custom"}, version="1")
    assert config is not None and config.endpoint == "http://b:2/custom"


@pytest.mark.parametrize("env", [
    {"OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"},
    {"OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/json"},
    {"OTEL_TRACES_SAMPLER": "nope"},
    {"OTEL_TRACES_SAMPLER": "traceidratio", "OTEL_TRACES_SAMPLER_ARG": "dos"},
    {"OTEL_TRACES_SAMPLER": "parentbased_traceidratio", "OTEL_TRACES_SAMPLER_ARG": "1.5"},
    {"OTEL_TRACES_EXPORTER": "zipkin"},
])
def test_invalid_values_are_startup_problems_and_never_echo_headers(env: dict[str, str]) -> None:
    with pytest.raises(ObservabilityConfigError) as info:
        tracing_config({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:1",
                        "OTEL_EXPORTER_OTLP_HEADERS": "authorization=SECRETO", **env}, version="1")
    assert info.value.problems and all("SECRETO" not in p for p in info.value.problems)
    assert "SECRETO" not in str(info.value)


def test_an_invalid_exporter_is_a_problem_even_without_an_endpoint() -> None:
    with pytest.raises(ObservabilityConfigError):
        tracing_config({"OTEL_TRACES_EXPORTER": "console"}, version="1")


def test_setup_installs_json_logs_on_root_and_quiets_the_sdk_loggers(root_logging: None) -> None:
    stream = io.StringIO()
    observability = setup_observability({}, version="1", stream=stream)
    try:
        assert observability.tracing is False and tel_setup._PROVIDER is None
        assert logging.getLogger().level == logging.INFO and len(_handlers()) == 1
        logging.getLogger("agentcore.api").warning("hola")
        assert json.loads(stream.getvalue().splitlines()[-1])["message"] == "hola"
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
    finally:
        observability.shutdown()
    assert _handlers() == []


def test_setting_up_twice_keeps_a_single_json_handler(root_logging: None) -> None:
    first = setup_observability({}, version="1", stream=io.StringIO())
    second = setup_observability({}, version="1", stream=io.StringIO())
    try:
        assert len(_handlers()) == 1
    finally:
        second.shutdown()
        first.shutdown()  # idempotent
    assert _handlers() == []


def test_an_endpoint_configures_resource_and_sampler_from_the_injected_env_only(
        monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")  # the process env is never read for this
    monkeypatch.setenv("OTEL_SERVICE_NAME", "del-proceso")
    before = trace.get_tracer_provider()
    observability = setup_observability({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
                                         "OTEL_SERVICE_NAME": "agentcore-prueba"},
                                        version="7", stream=io.StringIO())
    try:
        provider = tel_setup._PROVIDER
        assert observability.tracing and provider is not None
        assert provider.resource.attributes["service.name"] == "agentcore-prueba"
        assert provider.resource.attributes["service.version"] == "7"
        default = sampling.ParentBased(sampling.ALWAYS_ON)
        assert provider.sampler.get_description() == default.get_description()
        assert trace.get_tracer_provider() is before
    finally:
        observability.shutdown()
    assert tel_setup._PROVIDER is None


def test_shutdown_flushes_and_closes_the_exporter(root_logging: None) -> None:
    exporter = InMemorySpanExporter()
    observability = setup_observability({}, version="1", exporter=exporter, stream=io.StringIO())
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT):
        pass
    observability.shutdown()
    assert len(exporter.get_finished_spans()) == 1 and exporter._stopped  # type: ignore[attr-defined]
    assert tel_setup._PROVIDER is None


def _wire(monkeypatch: pytest.MonkeyPatch, seen: dict[str, Any]) -> list[bool]:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    seen["world"] = world

    def resolve(args: Any, env: Any, clock: Any, ids: Any) -> Any:
        return make_ports(world, issuer)

    monkeypatch.setattr(serve_module, "resolve_ports", resolve)
    shut: list[bool] = []
    real_setup = serve_module.setup_observability

    def setup(env: Any) -> Any:
        observability = real_setup(env, version="1", stream=io.StringIO())
        real_shutdown = observability.shutdown

        def shutdown() -> None:
            shut.append(True)
            real_shutdown()

        monkeypatch.setattr(observability, "shutdown", shutdown)
        return observability

    monkeypatch.setattr(serve_module, "setup_observability", setup)
    return shut


def test_run_serve_wires_observability_and_shuts_it_down(
        monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    seen: dict[str, Any] = {}
    shut = _wire(monkeypatch, seen)
    started: dict[str, Any] = {}
    world = seen["world"]
    code = serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  env={}, serve=lambda app, **kw: started.update(kw))
    assert code == 0 and shut == [True]
    assert started["log_config"] is None and started["access_log"] is False
    assert _handlers() == []


def test_run_serve_shuts_down_even_if_uvicorn_raises(
        monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    seen: dict[str, Any] = {}
    shut = _wire(monkeypatch, seen)
    world = seen["world"]

    def boom(app: object, **kwargs: Any) -> None:
        raise RuntimeError("uvicorn cayó")

    serve: Callable[..., None] = boom
    with pytest.raises(RuntimeError, match="uvicorn cayó"):
        serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                               env={}, serve=serve)
    assert shut == [True] and _handlers() == []


def test_run_serve_shuts_down_when_the_ports_are_invalid(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    seen: dict[str, Any] = {}
    shut = _wire(monkeypatch, seen)
    world = seen["world"]
    monkeypatch.setattr(serve_module, "resolve_ports", lambda *a, **k: (_ for _ in ()).throw(
        serve_module.ServeConfigError(["falta --dsn"])))
    code = serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  env={}, serve=lambda app, **kw: None)
    assert code == 2 and shut == [True] and "falta --dsn" in capsys.readouterr().err


def test_invalid_otel_configuration_exits_2(capsys: pytest.CaptureFixture[str], root_logging: None) -> None:
    world = EngineWorld()
    code = serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  serve=lambda app, **kw: None,
                                  env={"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:1",
                                       "OTEL_TRACES_SAMPLER": "nope",
                                       "OTEL_EXPORTER_OTLP_HEADERS": "authorization=SECRETO"})
    err = capsys.readouterr().err
    assert code == 2 and "OTEL_TRACES_SAMPLER" in err and "SECRETO" not in err
    assert _handlers() == [] and tel_setup._PROVIDER is None


# --- review of task 2: credentials, foreign root handlers, precedence and restore order ----------------


@pytest.mark.parametrize("entry", [
    "authorization=Bearer;SECRETO7", "x key=SECRETO2", 'clave=valor"SECRETO5', "clave=valor\\SECRETO6",
])
def test_a_header_entry_the_sdk_would_reject_is_a_startup_problem(entry: str) -> None:  # C1
    with pytest.raises(ObservabilityConfigError) as info:
        tracing_config({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:1",
                        "OTEL_EXPORTER_OTLP_HEADERS": f"ok=1,{entry}"}, version="1")
    assert info.value.problems == ["OTEL_EXPORTER_OTLP_HEADERS: la entrada 2 no es `clave=valor` válida"]


def test_a_credential_in_the_process_headers_never_reaches_stderr_or_logs(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture, root_logging: None, no_otel_env: None) -> None:  # C1, as serve
    import os

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=Bearer;SECRETO7")
    seen: dict[str, Any] = {}
    _wire(monkeypatch, seen)
    world = seen["world"]
    with caplog.at_level(logging.DEBUG):
        code = serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                      env=os.environ, serve=lambda app, **kw: None)
    captured = capsys.readouterr()
    assert code == 2 and "OTEL_EXPORTER_OTLP_HEADERS" in captured.err
    assert "SECRETO7" not in captured.err + captured.out + caplog.text


def test_the_sdk_never_logs_process_headers_while_the_exporter_is_built(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture, root_logging: None, no_otel_env: None) -> None:  # C1, the guard
    # The injected env is valid, but the OTLP exporter re-parses os.environ on its own and would log it.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x key=SECRETO2")
    stream = io.StringIO()
    with caplog.at_level(logging.DEBUG):
        observability = setup_observability({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9"},
                                            version="1", stream=stream)
        observability.shutdown()
    captured = capsys.readouterr()
    assert "SECRETO2" not in captured.err + captured.out + caplog.text + stream.getvalue()
    assert not logging.getLogger("opentelemetry.util.re").disabled  # the guard is undone


def test_the_traces_specific_protocol_wins_over_the_generic_one() -> None:  # M1
    env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:1"}
    assert tracing_config({**env, "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
                           "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf"}, version="1") is not None
    with pytest.raises(ObservabilityConfigError):
        tracing_config({**env, "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "grpc"}, version="1")


def test_a_foreign_root_handler_never_prints_an_exception_while_serving(root_logging: None) -> None:  # I1
    plain = io.StringIO()
    foreign = logging.StreamHandler(plain)  # e.g. OPENAI_LOG=debug -> logging.basicConfig at import
    foreign.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(foreign)
    json_stream = io.StringIO()
    observability = setup_observability({}, version="1", stream=json_stream)
    try:
        raise RuntimeError("SECRETO-X")
    except RuntimeError as exc:
        logging.getLogger("uvicorn.error").error("Exception in ASGI application", exc_info=exc)
    finally:
        observability.shutdown()
    assert "SECRETO-X" not in plain.getvalue() + json_stream.getvalue()
    assert json.loads(json_stream.getvalue().splitlines()[-1])["exc_type"] == "RuntimeError"
    assert foreign in logging.getLogger().handlers  # restored on shutdown


@pytest.mark.parametrize("order", ["first-then-second", "second-then-first"])
def test_two_setups_restore_the_original_logging_in_either_shutdown_order(order: str) -> None:  # M2
    root, sdk = logging.getLogger(), [logging.getLogger(n) for n in ("httpx", "httpcore")]
    before = (list(root.handlers), root.level, [lg.level for lg in sdk])
    first = setup_observability({}, version="1", stream=io.StringIO())
    second = setup_observability({}, version="1", stream=io.StringIO())
    try:
        assert len(_handlers()) == 1 and _handlers() == root.handlers
    finally:
        for observability in ((first, second) if order == "first-then-second" else (second, first)):
            observability.shutdown()
    assert (list(root.handlers), root.level, [lg.level for lg in sdk]) == before


def test_header_patterns_match_the_sdk() -> None:  # C1: our grammar must be the one the exporter applies
    from opentelemetry.util import re as sdk_re

    from agent_core.composition import observability

    assert observability._HEADER_PATTERN.pattern == sdk_re._HEADER_PATTERN.pattern
    assert observability._LIBERAL_HEADER_PATTERN.pattern == sdk_re._LIBERAL_HEADER_PATTERN.pattern
