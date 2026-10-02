"""Raíz de composición del servidor: `ServePorts` -> `ApiDeps` (motor real + API M9 + registry opcional)."""

import argparse
import sys
from collections.abc import Callable, Iterable, Mapping
from datetime import timedelta

from agent_core.adapters.llm import EndpointConfig
from agent_core.api.app import ApiDeps, create_app
from agent_core.api.security_log import OtelSecurityLog
from agent_core.audit import AuditLog
from agent_core.composition.engine import EngineDeps, build_engine
from agent_core.composition.observability import ObservabilityConfigError, setup_observability
from agent_core.composition.serve_ports import ServeConfigError, ServePorts, resolve_ports
from agent_core.composition.serve_registry import build_registry_service_for_serve
from agent_core.composition.telemetry import OtelTurnTelemetry
from agent_core.domain import (
    AgentSelector,
    AuthInfo,
    AuthLevel,
    EntityKind,
    EntityRef,
    ModelProfile,
    Principal,
    PrincipalType,
)
from agent_core.ports import Clock, IdSource, RegistryPort
from agent_core.registry import RegistryService
from agent_core.registry.http import registry_extension
from agent_core.turn import TurnTelemetry

GATEWAY_TRACER = "agent_core.adapters.llm"


def build_api_deps(ports: ServePorts, *, registry_service: RegistryService | None = None,
                   telemetry: TurnTelemetry | None = None) -> ApiDeps:
    built = build_engine(EngineDeps(
        clock=ports.clock, ids=ports.ids, keys=ports.keys, uow_factory=ports.uow_factory, audit=ports.audit,
        registry=ports.registry, releases=ports.releases, tools=ports.tools, gateway=ports.gateway,
        providers=ports.providers, calibrations=ports.calibrations, transcript=ports.transcript,
        authz=ports.authz, classifier=ports.classifier, telemetry=telemetry))
    return ApiDeps(
        verifier=ports.verifier, authz=ports.authz, registry=ports.registry, uow_factory=ports.uow_factory,
        counters=ports.counters, clock=ports.clock, ids=ports.ids, turns=built.turns,
        handoffs=built.handoffs, transcripts=built.transcripts,
        denials=AuditLog(ports.audit, ports.uow_factory), security=OtelSecurityLog(),
        extensions=() if registry_service is None else (registry_extension(
            registry_service, None if ports.registry_api is None else ports.registry_api.staff_verifier),))


def model_alias_warnings(registry: RegistryPort, agents: Iterable[str],
                         endpoints: Mapping[str, EndpointConfig], env: Mapping[str, str],
                         clock: Clock) -> list[str]:
    """Perfiles de la release `prod` de cada agente cuyo alias de LLM no tiene endpoint o cuya variable de key
    está vacía (gateway §5). Solo avisos: el arranque no se bloquea y nunca se imprime el valor de una key."""
    now = clock.now()
    startup = Principal(type=PrincipalType.service, id="serve-startup",
                        auth=AuthInfo(level=AuthLevel.session, at=now), exp=now + timedelta(minutes=1))
    warnings: list[str] = []
    for agent in agents:
        try:
            release = registry.resolve_release(AgentSelector(id=agent, alias="prod"), startup)
        except KeyError:
            warnings.append(f"el agente `{agent}` no tiene una release `prod` activa; "
                            "no se revisaron sus perfiles")
            continue
        for pid, version in sorted(release.entities.get(EntityKind.model_profile, {}).items()):
            profile = registry.get(EntityRef(id=pid, version=version), ModelProfile)
            endpoint = endpoints.get(profile.endpoint_alias)
            where = f"perfil {pid}@{version}, agente {agent}"
            if endpoint is None:
                warnings.append(f"alias de LLM `{profile.endpoint_alias}` sin endpoint en LLM_ENDPOINTS "
                                f"({where})")
            elif not env.get(endpoint.api_key_env, "").strip():
                warnings.append(
                    f"alias de LLM `{profile.endpoint_alias}`: la variable de API key está vacía ({where})"
                )
    return warnings


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
            ports = resolve_ports(args, env, clock, ids, tracer=observability.tracer(GATEWAY_TRACER))
        except ServeConfigError as exc:
            print("agentcore serve no puede arrancar:", file=sys.stderr)
            for problem in exc.problems:
                print(f"  - {problem}", file=sys.stderr)
            return 2
        if ports.doubles:
            print("AVISO: piezas que son DOBLES de demo (no producción): " + ", ".join(ports.doubles),
                  file=sys.stderr)
        for warning in model_alias_warnings(ports.registry, ports.agents, ports.endpoints, env, ports.clock):
            print(f"AVISO: {warning}", file=sys.stderr)
        registry_service = build_registry_service_for_serve(ports) if ports.registry_api is not None else None
        # Always the real turn telemetry: without an exporter its spans are no-ops, but `bind` still
        # correlates the turn's logs (m04 §3.9).
        app = create_app(
            build_api_deps(ports, registry_service=registry_service, telemetry=OtelTurnTelemetry()))
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
