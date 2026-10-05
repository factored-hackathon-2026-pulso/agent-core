"""Raíz de composición del servidor: `ServePorts` -> `ApiDeps` (motor real + API M9 + registry opcional)."""

import argparse
import logging
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from agent_core.adapters.llm import gateway_is_up
from agent_core.adapters.llm.http_gateway import LLM_GATEWAY_URL_ENV
from agent_core.api.app import ApiDeps, ApiExtension, create_app
from agent_core.api.limits import RateLimitConfig
from agent_core.api.security_log import OtelSecurityLog
from agent_core.audit import AuditLog
from agent_core.composition.builder_tools import BuilderToolExecutor, RoutedTools
from agent_core.composition.engine import EngineConfig, EngineDeps, EngineTools, build_engine
from agent_core.composition.export_http import export_extension
from agent_core.composition.observability import ObservabilityConfigError, setup_observability
from agent_core.composition.serve_ports import (
    DOUBLES_ENV,
    LEGACY_DOUBLES_ENV,
    ServeConfigError,
    ServePorts,
    resolve_ports,
)
from agent_core.composition.serve_registry import build_registry_service_for_serve
from agent_core.composition.telemetry import OtelTurnTelemetry
from agent_core.domain import (
    AgentSelector,
    AuthInfo,
    AuthLevel,
    Principal,
    PrincipalType,
)
from agent_core.ports import Clock, IdSource, RegistryPort
from agent_core.registry import RegistryService
from agent_core.registry.http import registry_extension
from agent_core.turn import TurnTelemetry


def _extensions(ports: ServePorts, registry_service: RegistryService | None) -> tuple[ApiExtension, ...]:
    """La API del registry y, con verificador del staff y un almacén que exporta, la exportación (N-08)."""
    if registry_service is None:
        return ()
    staff = None if ports.registry_api is None else ports.registry_api.staff_verifier
    found: list[ApiExtension] = [registry_extension(registry_service, staff, ports.clock)]
    if staff is not None and ports.run_export is not None:
        found.append(export_extension(ports.run_export, registry_service, staff, ports.clock))
    return tuple(found)


_LOG = logging.getLogger("agent_core.serve")


def mode_line(ports: ServePorts) -> str:
    """The startup mode: `production` when no piece is a double, else `demo` with the doubles named."""
    if not ports.doubles:
        return "serve mode=production"
    return "serve mode=demo doubles=" + ",".join(ports.doubles)


RATE_MAX_HITS_ENV = "AGENTCORE_RATE_MAX_HITS"
RATE_WINDOW_ENV = "AGENTCORE_RATE_WINDOW_SECONDS"
DAILY_BUDGET_ENV = "AGENTCORE_DAILY_BUDGET_USD"
SERVICE_MULTIPLIER_ENV = "AGENTCORE_RATE_SERVICE_MULTIPLIER"


def rate_limits_from_env(env: Mapping[str, str]) -> RateLimitConfig:
    """Los límites por principal de la API; lo no definido conserva el valor de demo. `ValueError` si una
    variable no es un número válido (el arranque lo informa y sale)."""
    defaults = RateLimitConfig()
    try:
        return RateLimitConfig(
            window=timedelta(seconds=float(env.get(RATE_WINDOW_ENV) or defaults.window.total_seconds())),
            max_hits=int(env.get(RATE_MAX_HITS_ENV) or defaults.max_hits),
            daily_budget_usd=Decimal(env.get(DAILY_BUDGET_ENV) or defaults.daily_budget_usd),
            service_multiplier=int(env.get(SERVICE_MULTIPLIER_ENV) or defaults.service_multiplier))
    except (ArithmeticError, ValueError) as exc:
        raise ValueError(f"límites de tasa inválidos ({RATE_MAX_HITS_ENV}, {RATE_WINDOW_ENV}, "
                         f"{DAILY_BUDGET_ENV}, {SERVICE_MULTIPLIER_ENV})") from exc


def build_api_deps(ports: ServePorts, *, registry_service: RegistryService | None = None,
                   telemetry: TurnTelemetry | None = None, build_sha: str | None = None,
                   limits: RateLimitConfig | None = None) -> ApiDeps:
    built = build_engine(EngineDeps(
        clock=ports.clock, ids=ports.ids, keys=ports.keys, uow_factory=ports.uow_factory, audit=ports.audit,
        registry=ports.registry, releases=ports.releases, tools=ports.tools, gateway=ports.gateway,
        providers=ports.providers, calibrations=ports.calibrations, transcript=ports.transcript,
        authz=ports.authz, classifier=ports.classifier, directory=ports.directory, telemetry=telemetry,
        config=EngineConfig(lang_thresholds=ports.lang_thresholds),
        # Fuera de demo el motor sirve sus propias tools; los dobles de demo ya las traen guionadas.
        engine_tools=EngineTools(fx_rates=ports.fx_rates) if ports.engine_tools else None))
    return ApiDeps(
        verifier=ports.verifier, authz=ports.authz, registry=ports.registry, uow_factory=ports.uow_factory,
        counters=ports.counters, clock=ports.clock, ids=ports.ids, turns=built.turns,
        handoffs=built.handoffs, transcripts=built.transcripts,
        denials=AuditLog(ports.audit, ports.uow_factory), security=OtelSecurityLog(),
        readiness=ports.readiness, optional_checks=ports.optional_readiness, build_sha=build_sha,
        limits=limits or RateLimitConfig(),
        extensions=_extensions(ports, registry_service))


def constructor_bot(clock: Clock) -> Principal:
    """Identidad de servicio del constructor: `builder` con solo el rol `constructor`, sin `actor` humano."""
    now = clock.now()
    return Principal(type=PrincipalType.builder, id="constructor-bot", roles=["constructor"], attrs={},
                     auth=AuthInfo(level=AuthLevel.session, at=now), exp=now + timedelta(days=3650))


def release_warnings(registry: RegistryPort, agents: Iterable[str], clock: Clock) -> list[str]:
    """Agents named for the startup check that have no active `prod` release. Warnings only: startup is never
    blocked. The model aliases are no longer checked here: they live in the llm-gateway service."""
    now = clock.now()
    startup = Principal(type=PrincipalType.service, id="serve-startup",
                        auth=AuthInfo(level=AuthLevel.session, at=now), exp=now + timedelta(minutes=1))
    warnings: list[str] = []
    for agent in agents:
        try:
            registry.resolve_release(AgentSelector(id=agent, alias="prod"), startup)
        except KeyError:
            warnings.append(f"el agente `{agent}` no tiene una release `prod` activa")
    return warnings


def gateway_warnings(url: str | None) -> list[str]:
    """Startup notes about the llm-gateway: not configured, or not answering `/healthz`. The URL is not
    printed (it can carry credentials)."""
    if url is None:
        return [f"{LLM_GATEWAY_URL_ENV} sin definir: sin llm-gateway toda generación cae a plantilla"]
    if not gateway_is_up(url):
        return ["el llm-gateway configurado no responde en /healthz; sus generaciones fallarán"]
    return []


def run_serve(args: argparse.Namespace, *, clock: Clock, ids: IdSource, env: Mapping[str, str],
              serve: Callable[..., None] | None = None) -> int:
    """Configura la observabilidad, resuelve los puertos, avisa de los dobles y arranca uvicorn. `serve` se
    inyecta en las pruebas. La observabilidad se apaga siempre al salir (vacía el exportador, I6)."""
    try:
        observability = setup_observability(env)
    except ObservabilityConfigError as exc:
        print("agentcore serve no puede arrancar:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    try:
        limits = rate_limits_from_env(env)
    except ValueError as exc:
        print("agentcore serve no puede arrancar:", file=sys.stderr)
        print(f"  - {exc}", file=sys.stderr)
        observability.shutdown()
        return 2
    try:
        try:
            ports = resolve_ports(args, env, clock, ids)
        except ServeConfigError as exc:
            print("agentcore serve no puede arrancar:", file=sys.stderr)
            for problem in exc.problems:
                print(f"  - {problem}", file=sys.stderr)
            return 2
        _LOG.info(mode_line(ports))
        if env.get(LEGACY_DOUBLES_ENV) == "1" and env.get(DOUBLES_ENV) != "1":
            print(f"AVISO: {LEGACY_DOUBLES_ENV} está en desuso; usa {DOUBLES_ENV}=1", file=sys.stderr)
        if ports.doubles:
            print("AVISO: piezas que son DOBLES de demo (no producción): " + ", ".join(ports.doubles),
                  file=sys.stderr)
        for warning in (*release_warnings(ports.registry, ports.agents, ports.clock),
                        *gateway_warnings(ports.llm_gateway_url)):
            print(f"AVISO: {warning}", file=sys.stderr)
        registry_service = build_registry_service_for_serve(ports) if ports.registry_api is not None else None
        # Always the real turn telemetry: without an exporter its spans are no-ops, but `bind` still
        # correlates the turn's logs (m04 §3.9).
        if registry_service is not None:  # ADR 0019 §4: el constructor escribe con su credencial de servicio
            ports = replace(ports, tools=RoutedTools(
                BuilderToolExecutor(registry_service, constructor_bot(ports.clock), ports.ids), ports.tools))
        app = create_app(
            build_api_deps(ports, registry_service=registry_service, telemetry=OtelTurnTelemetry(),
                           build_sha=env.get("AGENTCORE_GIT_SHA") or None, limits=limits))
        if serve is None:
            import uvicorn

            serve = uvicorn.run
        # F5: uvicorn's own logging config would print "Exception in ASGI application" with the exception's
        # message and stack (Starlette re-raises after the 500 handler); with `log_config=None` it reaches the
        # root JSON formatter, which keeps only `exc_type`. The access log would print client IPs and id
        # paths.
        serve(app, host=args.host, port=args.port, log_config=None, access_log=False)
        return 0
    finally:
        observability.shutdown()  # I6: flush the batch exporter on exit
