"""Raíz de composición del servidor: `ServePorts` -> `ApiDeps` (motor real + API M9 + registry opcional)."""

import argparse
import logging
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal

from fastapi import FastAPI

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
from agent_core.composition.schema_version import SchemaBootstrap
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
AUTO_MIGRATE_ENV = "AGENTCORE_AUTO_MIGRATE"  # "0": no migrar al arrancar (el rol sin DDL)


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


MAX_INFLIGHT_ENV = "AGENTCORE_MAX_INFLIGHT"
WORKER_THREADS_ENV = "AGENTCORE_WORKER_THREADS"
SHUTDOWN_GRACE_ENV = "AGENTCORE_SHUTDOWN_GRACE_SECONDS"


@dataclass(frozen=True)
class OpsConfig:
    max_inflight: int = 0  # 0 = sin tope
    worker_threads: int = 40  # hilos de las rutas síncronas (el valor por defecto de anyio)
    shutdown_grace_s: float = 25.0  # plazo de uvicorn para las peticiones en curso al recibir SIGTERM


def ops_from_env(env: Mapping[str, str]) -> OpsConfig:
    """Topes de carga y plazo de apagado; lo no definido conserva el valor por defecto. `ValueError` con el
    nombre de la variable inválida (el arranque lo informa y sale)."""
    defaults = OpsConfig()

    def number(name: str, default: float, cast: Callable[[str], float], minimum: float) -> float:
        raw = env.get(name)
        if not raw:
            return default
        try:
            value = cast(raw)
        except ValueError:
            raise ValueError(f"{name} debe ser un número") from None
        if value < minimum:
            raise ValueError(f"{name} debe ser >= {minimum:g}")
        return value

    raw_pool = env.get("AGENTCORE_DB_POOL_MAX") or ""
    pool = int(raw_pool) if raw_pool.isdigit() else 0  # un valor inválido lo rechaza `resolve_ports`
    # Un turno retiene una conexión y pide otra (UoW + auditoría): con más turnos simultáneos que la mitad del
    # pool se agota (`PoolTimeout`, 30 s). Sin tope explícito, el tope sigue al pool.
    derived = max(1, pool // 2) if pool > 0 else defaults.max_inflight
    return OpsConfig(
        max_inflight=int(number(MAX_INFLIGHT_ENV, derived, int, 0)),
        worker_threads=int(number(WORKER_THREADS_ENV, defaults.worker_threads, int, 1)),
        shutdown_grace_s=number(SHUTDOWN_GRACE_ENV, defaults.shutdown_grace_s, float, 0))


def build_api_deps(ports: ServePorts, *, registry_service: RegistryService | None = None,
                   telemetry: TurnTelemetry | None = None, build_sha: str | None = None,
                   limits: RateLimitConfig | None = None, max_inflight: int = 0) -> ApiDeps:
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
        max_inflight=max_inflight, limits=limits or RateLimitConfig(),
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


def startup_release_warnings(ports: ServePorts) -> list[str]:
    """`release_warnings` that never keeps the process from starting: with Postgres down the check is skipped
    (`/readyz` already says 503) and any other failure of the check is itself a warning naming only its type
    (a database error can carry a host or a credential)."""
    if not ports.agents:
        return []
    database = dict(ports.readiness).get("postgres")
    if database is not None and not database():
        return ["la base no responde: no se revisan las releases `prod` al arrancar "
                "(`/readyz` da 503 hasta que responda)"]
    try:
        return release_warnings(ports.registry, ports.agents, ports.clock)
    except Exception as exc:
        names = ", ".join(ports.agents)
        return [f"no se pudieron revisar las releases `prod` de {names}: {type(exc).__name__}"]


def gateway_warnings(url: str | None) -> list[str]:
    """Startup notes about the llm-gateway: not configured, or not answering `/healthz`. The URL is not
    printed (it can carry credentials)."""
    if url is None:
        return [f"{LLM_GATEWAY_URL_ENV} sin definir: sin llm-gateway toda generación cae a plantilla"]
    if not gateway_is_up(url):
        return ["el llm-gateway configurado no responde en /healthz; sus generaciones fallarán"]
    return []


def pool_warnings(pool_max: int, max_inflight: int) -> list[str]:
    """Un turno retiene una conexión y pide otra: `max_inflight` turnos simultáneos piden 2 x `max_inflight`
    conexiones. Si el pool es menor (o no hay tope de peticiones), bajo carga hay `PoolTimeout` (A12)."""
    if pool_max <= 0:
        return []
    if max_inflight <= 0:
        return ["AGENTCORE_DB_POOL_MAX sin AGENTCORE_MAX_INFLIGHT: con carga los turnos simultáneos pueden "
                "agotar el pool (PoolTimeout); ver docs/serve-env.md (dimensionamiento)"]
    if 2 * max_inflight > pool_max:
        return [f"AGENTCORE_MAX_INFLIGHT={max_inflight} necesita {2 * max_inflight} conexiones pero "
                f"AGENTCORE_DB_POOL_MAX={pool_max}: bajo carga habrá PoolTimeout; ver docs/serve-env.md"]
    return []


def install_worker_threads(app: FastAPI, total: int) -> None:
    """Hilos de las rutas síncronas: el límite por defecto de anyio (40) acota la concurrencia. Se aplica al
    arrancar la app, dentro del bucle de eventos (el manejador tiene que ser `async def`, no un lambda)."""
    from anyio import to_thread

    async def apply() -> None:
        to_thread.current_default_thread_limiter().total_tokens = total

    app.router.on_startup.append(apply)


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
        ops = ops_from_env(env)
    except ValueError as exc:
        print("agentcore serve no puede arrancar:", file=sys.stderr)
        print(f"  - {exc}", file=sys.stderr)
        observability.shutdown()
        return 2
    bootstrap: SchemaBootstrap | None = None
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
        raw_pool = env.get("AGENTCORE_DB_POOL_MAX") or ""
        pool_size = int(raw_pool) if raw_pool.isdigit() else 0
        for warning in (*startup_release_warnings(ports), *gateway_warnings(ports.llm_gateway_url),
                        *pool_warnings(pool_size, ops.max_inflight)):
            print(f"AVISO: {warning}", file=sys.stderr)
        if ports.migrate is not None and env.get(AUTO_MIGRATE_ENV) != "0":
            # En segundo plano y con reintentos: sin base `serve` arranca igual y `/readyz` dice 503.
            migrate = ports.migrate
            bootstrap = SchemaBootstrap(
                lambda: _LOG.info("schema migrated=%s", ",".join(migrate()) or "none"))
            bootstrap.start()
        registry_service = build_registry_service_for_serve(ports) if ports.registry_api is not None else None
        # Always the real turn telemetry: without an exporter its spans are no-ops, but `bind` still
        # correlates the turn's logs (m04 §3.9).
        if registry_service is not None:  # ADR 0019 §4: el constructor escribe con su credencial de servicio
            ports = replace(ports, tools=RoutedTools(
                BuilderToolExecutor(registry_service, constructor_bot(ports.clock), ports.ids), ports.tools))
        app = create_app(
            build_api_deps(ports, registry_service=registry_service, telemetry=OtelTurnTelemetry(),
                           build_sha=env.get("AGENTCORE_GIT_SHA") or None, limits=limits,
                           max_inflight=ops.max_inflight))
        install_worker_threads(app, ops.worker_threads)
        if serve is None:
            import uvicorn

            serve = uvicorn.run
        # F5: uvicorn's own logging config would print "Exception in ASGI application" with the exception's
        # message and stack (Starlette re-raises after the 500 handler); with `log_config=None` it reaches the
        # root JSON formatter, which keeps only `exc_type`. The access log would print client IPs and id
        # paths.
        serve(app, host=args.host, port=args.port, log_config=None, access_log=False,
              timeout_graceful_shutdown=int(ops.shutdown_grace_s))
        return 0
    finally:
        if bootstrap is not None:
            bootstrap.stop()
        observability.shutdown()  # I6: flush the batch exporter on exit
