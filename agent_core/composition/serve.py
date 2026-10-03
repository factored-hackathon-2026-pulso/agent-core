"""Raíz de composición del servidor: `ServePorts` -> `ApiDeps` (motor real + API M9 + registry opcional)."""

import argparse
import sys
from collections.abc import Callable, Iterable, Mapping
from datetime import timedelta

from agent_core.adapters.llm import gateway_is_up
from agent_core.adapters.llm.http_gateway import LLM_GATEWAY_URL_ENV
from agent_core.api.app import ApiDeps, ApiExtension, create_app
from agent_core.api.security_log import OtelSecurityLog
from agent_core.audit import AuditLog
from agent_core.composition.engine import EngineDeps, build_engine
from agent_core.composition.export_http import export_extension
from agent_core.composition.observability import ObservabilityConfigError, setup_observability
from agent_core.composition.serve_ports import ServeConfigError, ServePorts, resolve_ports
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


def build_api_deps(ports: ServePorts, *, registry_service: RegistryService | None = None,
                   telemetry: TurnTelemetry | None = None, build_sha: str | None = None) -> ApiDeps:
    built = build_engine(EngineDeps(
        clock=ports.clock, ids=ports.ids, keys=ports.keys, uow_factory=ports.uow_factory, audit=ports.audit,
        registry=ports.registry, releases=ports.releases, tools=ports.tools, gateway=ports.gateway,
        providers=ports.providers, calibrations=ports.calibrations, transcript=ports.transcript,
        authz=ports.authz, classifier=ports.classifier, directory=ports.directory, telemetry=telemetry))
    return ApiDeps(
        verifier=ports.verifier, authz=ports.authz, registry=ports.registry, uow_factory=ports.uow_factory,
        counters=ports.counters, clock=ports.clock, ids=ports.ids, turns=built.turns,
        handoffs=built.handoffs, transcripts=built.transcripts,
        denials=AuditLog(ports.audit, ports.uow_factory), security=OtelSecurityLog(),
        readiness=ports.readiness, build_sha=build_sha,
        extensions=_extensions(ports, registry_service))


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
        try:
            ports = resolve_ports(args, env, clock, ids)
        except ServeConfigError as exc:
            print("agentcore serve no puede arrancar:", file=sys.stderr)
            for problem in exc.problems:
                print(f"  - {problem}", file=sys.stderr)
            return 2
        if ports.doubles:
            print("AVISO: piezas que son DOBLES de demo (no producción): " + ", ".join(ports.doubles),
                  file=sys.stderr)
        for warning in (*release_warnings(ports.registry, ports.agents, ports.clock),
                        *gateway_warnings(ports.llm_gateway_url)):
            print(f"AVISO: {warning}", file=sys.stderr)
        registry_service = build_registry_service_for_serve(ports) if ports.registry_api is not None else None
        # Always the real turn telemetry: without an exporter its spans are no-ops, but `bind` still
        # correlates the turn's logs (m04 §3.9).
        app = create_app(
            build_api_deps(ports, registry_service=registry_service, telemetry=OtelTurnTelemetry(),
                           build_sha=env.get("AGENTCORE_GIT_SHA") or None))
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
