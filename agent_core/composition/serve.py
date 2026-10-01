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
from agent_core.composition.serve_ports import ServeConfigError, ServePorts, resolve_ports
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


def build_api_deps(ports: ServePorts, *, registry_service: RegistryService | None = None) -> ApiDeps:
    built = build_engine(EngineDeps(
        clock=ports.clock, ids=ports.ids, keys=ports.keys, uow_factory=ports.uow_factory, audit=ports.audit,
        registry=ports.registry, releases=ports.releases, tools=ports.tools, gateway=ports.gateway,
        providers=ports.providers, calibrations=ports.calibrations, transcript=ports.transcript,
        authz=ports.authz, classifier=ports.classifier))
    return ApiDeps(
        verifier=ports.verifier, authz=ports.authz, registry=ports.registry, uow_factory=ports.uow_factory,
        counters=ports.counters, clock=ports.clock, ids=ports.ids, turns=built.turns,
        handoffs=built.handoffs, transcripts=built.transcripts,
        denials=AuditLog(ports.audit, ports.uow_factory), security=OtelSecurityLog(),
        extensions=() if registry_service is None else (registry_extension(registry_service),))


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
                warnings.append(f"alias de LLM `{profile.endpoint_alias}`: la variable "
                                f"{endpoint.api_key_env} está vacía ({where})")
    return warnings


def run_serve(args: argparse.Namespace, *, clock: Clock, ids: IdSource, env: Mapping[str, str],
              serve: Callable[..., None] | None = None) -> int:
    """Resuelve los puertos, avisa de los dobles y arranca uvicorn. `serve` se inyecta en las pruebas."""
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
    for warning in model_alias_warnings(ports.registry, ports.agents, ports.endpoints, env, ports.clock):
        print(f"AVISO: {warning}", file=sys.stderr)
    app = create_app(build_api_deps(ports))
    if serve is None:
        import uvicorn

        serve = uvicorn.run
    serve(app, host=args.host, port=args.port)
    return 0
