"""Raíz de composición del servidor: `ServePorts` -> `ApiDeps` (motor real + API M9 + registry opcional)."""

import argparse
import sys
from collections.abc import Callable, Mapping

from agent_core.api.app import ApiDeps, create_app
from agent_core.api.security_log import OtelSecurityLog
from agent_core.audit import AuditLog
from agent_core.composition.engine import EngineDeps, build_engine
from agent_core.composition.serve_ports import ServeConfigError, ServePorts, resolve_ports
from agent_core.ports import Clock, IdSource
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
    app = create_app(build_api_deps(ports))
    if serve is None:
        import uvicorn

        serve = uvicorn.run
    serve(app, host=args.host, port=args.port)
    return 0
