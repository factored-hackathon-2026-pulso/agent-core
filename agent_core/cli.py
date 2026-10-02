"""CLI `agentcore`: `contracts`, `validate`, `sweep`, `replay`, `record`, `llm-smoke`, `registry`, `serve`,
`migrate`."""

import argparse
import importlib
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import psycopg

from agent_core.actions import ActionManager
from agent_core.adapters.llm import LLMAgentPort, OpenAICompatGateway, load_endpoints
from agent_core.adapters.llm.smoke import (
    SMOKE_PROMPT,
    AgentStepRunner,
    AgentStepUsage,
    SmokeRegistry,
    format_report,
    run_smoke,
    smoke_agent_config,
)
from agent_core.adapters.postgres_uow import PostgresStore
from agent_core.adapters.system_clock import SystemClock
from agent_core.adapters.system_ids import SystemIds
from agent_core.api.openapi import check_openapi, write_openapi
from agent_core.audit import (
    AuditLog,
    EngineRunner,
    FixtureRejected,
    Replayer,
    ReplayReport,
    check_chain,
    check_fixture,
    dump_fixture,
    load_catalog,
    load_fixture_file,
)
from agent_core.audit.replay.cli_support import EXIT, USAGE_ERROR, render_report
from agent_core.contracts import check_contracts, write_contracts
from agent_core.domain import (
    AgentSelector,
    DomainError,
    EntityKind,
    EntityRef,
    Locale,
    ModelProfile,
    Principal,
    RegistryEntity,
    Release,
    RunState,
    SchemaError,
)
from agent_core.flows import AuthoringRegistry, load_registry
from agent_core.flows.cli_validate import run_validate
from agent_core.interpreter import AgentRequest
from agent_core.ports import Clock
from agent_core.turn import Sweeper, SweepReport

ENGINE_UNAVAILABLE_MESSAGE = "motor no disponible: el replay necesita el motor integrado y --registry"
RECORD_UNAVAILABLE_MESSAGE = "motor no disponible: record necesita el motor integrado y --registry"
# El motor del replay y `record` son herramientas de desarrollo (`testing/`, no van al wheel).
_ENGINE_MODULE = "testing.replay"
DSN_ENV = "AGENTCORE_DATABASE_URL"


class EngineUnavailable(RuntimeError):
    """El motor del replay no está disponible: falta `testing/replay` (wheel instalado) o `--registry`."""


def load_engine(registry: Path | None = None) -> EngineRunner:
    """Construye el `EngineRunner` del replay sobre el registro de autoría `registry`."""
    try:
        module = importlib.import_module(_ENGINE_MODULE)
    except ModuleNotFoundError as error:
        if error.name not in (_ENGINE_MODULE, _ENGINE_MODULE.split(".")[0]):
            raise  # un fallo interno del motor no es "motor no disponible"
        raise EngineUnavailable from error
    if registry is None:
        raise EngineUnavailable
    runner: EngineRunner = module.build_engine_runner(registry)
    return runner


def _run_replay(args: argparse.Namespace) -> int:
    target = Path(args.target)
    if not target.is_file():
        print(f"replay por run_id necesita un almacén de auditoría y el motor: {ENGINE_UNAVAILABLE_MESSAGE}",
              file=sys.stderr)
        return USAGE_ERROR
    try:
        fixture = load_fixture_file(target)
        if args.catalog is not None:
            check_fixture(fixture, load_catalog(args.catalog))
    except FixtureRejected as rejected:
        print(f"fixture con datos no sintéticos: {rejected}", file=sys.stderr)
        return USAGE_ERROR
    except Exception as error:
        print(f"fixture ilegible: {type(error).__name__}", file=sys.stderr)
        return USAGE_ERROR
    clock = SystemClock()
    check = check_chain(fixture.run_id, list(fixture.events))
    if not check.ok:
        report = ReplayReport(mode=args.mode, run_id=fixture.run_id, release=fixture.release,
                              verdict="chain_broken", chain_broken_at=check.broken_at,
                              duration_ms=None)
        print(render_report(report, args.json))
        return EXIT["chain_broken"]
    try:
        engine = load_engine(args.registry)
    except EngineUnavailable:
        print(ENGINE_UNAVAILABLE_MESSAGE, file=sys.stderr)
        return USAGE_ERROR
    try:
        result = Replayer(engine, clock, definitions=getattr(engine, "definitions", None)).replay(
            fixture, args.mode)
    except Exception as error:
        print(f"replay falló: {type(error).__name__}", file=sys.stderr)
        return USAGE_ERROR
    print(render_report(result, args.json))
    return EXIT[result.verdict]


def _run_record(args: argparse.Namespace) -> int:
    try:
        load_engine(args.registry)
    except EngineUnavailable:
        print(RECORD_UNAVAILABLE_MESSAGE, file=sys.stderr)
        return USAGE_ERROR
    scenarios = importlib.import_module(_ENGINE_MODULE).SCENARIOS
    if args.scenario not in scenarios:
        print(f"escenario desconocido: {args.scenario} (disponibles: {', '.join(sorted(scenarios))})",
              file=sys.stderr)
        return USAGE_ERROR
    try:
        fixture = importlib.import_module(_ENGINE_MODULE).record_scenario(args.scenario, args.registry)
        if args.catalog is not None:
            check_fixture(fixture, load_catalog(args.catalog))
    except FixtureRejected as rejected:
        print(f"el fixture grabado tiene datos no sintéticos: {rejected}", file=sys.stderr)
        return USAGE_ERROR
    args.out.write_text(dump_fixture(fixture), encoding="utf-8")
    print(f"record: {args.scenario} -> {args.out}")
    return 0


# --- composición de `agentcore sweep`: Postgres real, registro de autoría en disco y reloj del sistema -----
# El registry de la unidad 2 aún no existe: el barrido solo necesita el `Agent` de cada run, que se lee del
# directorio de autoría por versión exacta.


class _AuthoringAgents:
    """`RegistryPort` mínimo para el barrido: solo `get` (versiones exactas del directorio de autoría)."""

    def __init__(self, registry: AuthoringRegistry) -> None:
        self._registry = registry

    def get[T: RegistryEntity](self, ref: EntityRef, kind: type[T]) -> T:
        for entity_kind in EntityKind:
            try:
                entity = self._registry.get_exact(entity_kind, ref.id, ref.version)
            except KeyError:
                continue
            if isinstance(entity, kind):
                return entity
        raise SchemaError(f"{ref.id}@{ref.version} no está en el registro")

    def resolve_release(self, selector: AgentSelector, principal: Principal) -> Release:
        raise NotImplementedError("el barrido no resuelve releases")

    def release_status(self, release_id: str) -> str:
        raise NotImplementedError("el barrido no consulta el estado de las releases")


def build_sweeper(dsn: str, registry_root: Path) -> Sweeper:
    registry, violations = load_registry(registry_root)
    if violations:
        raise SchemaError(f"registro inválido: {len(violations)} violaciones (agentcore validate)")
    store = PostgresStore(dsn)
    clock, ids = SystemClock(), SystemIds()
    return Sweeper(
        uow_factory=store.uow, registry=_AuthoringAgents(registry), clock=clock, ids=ids,  # type: ignore[arg-type]
        actions=ActionManager(ids, clock), chain=AuditLog(store.audit(), store.uow))


class SweeperLike(Protocol):
    def sweep(self, now: Any) -> SweepReport: ...


def _run_sweep(args: argparse.Namespace, sweeper: SweeperLike | None, clock: Clock | None) -> int:
    dsn = args.dsn or os.environ.get(DSN_ENV)
    if sweeper is None:
        if not dsn or args.registry is None:
            print(f"agentcore sweep necesita --dsn (o {DSN_ENV}) y --registry", file=sys.stderr)
            return 2
        try:
            sweeper = build_sweeper(dsn, args.registry)
        except SchemaError as error:
            print(f"registro inválido: {error}", file=sys.stderr)
            return 1
    try:
        report = sweeper.sweep((clock or SystemClock()).now())
    except psycopg.Error as error:  # el mensaje de psycopg puede traer la DSN: solo la clase
        print(f"sweep: falla de Postgres ({type(error).__name__})", file=sys.stderr)
        return 1
    except SchemaError as error:
        print(f"sweep: {error}", file=sys.stderr)
        return 1
    print(f"evaluated={report.evaluated} abandoned={report.abandoned} skipped={report.skipped}")
    return 0


def _smoke_state(locale: Locale, now: datetime) -> RunState:
    """Estado sintético mínimo: `LLMAgentPort` solo lee el locale."""
    return RunState.model_validate({
        "run_id": "llm-smoke", "release": "llm-smoke", "agent": "llm-smoke@0.0.0",
        "principal": {"type": "service", "id": "llm-smoke", "auth": {"level": "session", "at": now},
                      "exp": now + timedelta(minutes=30)}, "mode": "conversational", "locale": locale,
        "created_at": now, "last_activity_at": now, "inactive_after": now + timedelta(minutes=30)})


def _agent_step_runner(port: LLMAgentPort, clock: Clock) -> AgentStepRunner:
    config = smoke_agent_config()

    def run(step: int, locale: Locale) -> AgentStepUsage:
        result = port.step(AgentRequest(node_id="llm-smoke", config=config, step=step),
                           _smoke_state(locale, clock.now()))
        return AgentStepUsage(tokens=result.tokens, cost_usd=result.cost_usd)

    return run


def _run_llm_smoke(args: argparse.Namespace) -> int:
    """Prueba de humo del gateway: nunca imprime la clave ni el contenido generado."""
    # El SDK `openai` registra los cuerpos de request en DEBUG (incluso con OPENAI_LOG=debug): se corta.
    for name in ("openai", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)
    if args.n < 1:
        print("--n debe ser al menos 1", file=sys.stderr)
        return USAGE_ERROR
    try:
        endpoints = load_endpoints(os.environ)
        if not endpoints:
            print("llm-smoke necesita LLM_ENDPOINTS con al menos un endpoint", file=sys.stderr)
            return USAGE_ERROR
        try:
            profile = EntityRef.parse(args.profile)
        except DomainError:
            print("--profile debe ser id@versión exacta", file=sys.stderr)
            return USAGE_ERROR
    except SchemaError as error:
        print(str(error), file=sys.stderr)
        return USAGE_ERROR
    registry, violations = load_registry(args.registry)
    if violations:
        print(f"registro con {len(violations)} violaciones (agentcore validate); sigue si el perfil existe",
              file=sys.stderr)
    agents = _AuthoringAgents(registry)  # solo se usa `get`; ver `build_sweeper`
    try:
        agents.get(profile, ModelProfile)
    except SchemaError:
        print(f"el perfil {profile} no está en el registro {args.registry}", file=sys.stderr)
        return USAGE_ERROR
    smoke_registry = SmokeRegistry(agents, profile)  # type: ignore[arg-type]
    gateway = OpenAICompatGateway(smoke_registry, endpoints, os.environ)
    clock = SystemClock()
    port = LLMAgentPort(gateway, smoke_registry, lambda kind, ref: ref.require_exact())
    report = run_smoke(gateway, SMOKE_PROMPT, clock, n=args.n, agent_step=_agent_step_runner(port, clock))
    print(format_report(report))
    return 0 if report.ok > 0 else 1


def main(
    argv: Sequence[str] | None = None, *, sweeper: SweeperLike | None = None, clock: Clock | None = None
) -> int:
    parser = argparse.ArgumentParser(prog="agentcore")
    sub = parser.add_subparsers(dest="command", required=True)
    contracts = sub.add_parser("contracts", help="genera o verifica contracts/")
    contracts.add_argument("--check", action="store_true", help="falla si contracts/ está desactualizado")
    contracts.add_argument("--out", type=Path, default=Path("contracts"))
    validate = sub.add_parser("validate", help="valida un registro de autoría (M1)")
    validate.add_argument("root", type=Path)
    validate.add_argument("--json", action="store_true", help="salida JSON estable")
    sweep = sub.add_parser("sweep", help="cierra como abandoned los runs vencidos (M4)")
    sweep.add_argument("--registry", type=Path, default=None, help="directorio del registro de autoría")
    sweep.add_argument("--dsn", default=None,
                       help=f"DSN de Postgres (o la variable {DSN_ENV}); no se imprime nunca")
    sweep.add_argument("--once", action="store_true", help="una sola pasada (por ahora es la única)")
    replay = sub.add_parser("replay", help="reproduce un run grabado y compara los eventos (M11)")
    replay.add_argument("target", help="ruta de un fixture YAML o run_id")
    replay.add_argument("--mode", choices=["fixture", "audit"], required=True)
    replay.add_argument("--json", action="store_true", help="salida JSON estable")
    replay.add_argument("--catalog", type=Path, default=None, help="catálogo de datos de prueba")
    replay.add_argument("--registry", type=Path, default=None,
                        help="directorio del registro de autoría de la release grabada")
    record = sub.add_parser("record", help="graba un fixture de un camino (M11)")
    record.add_argument("scenario")
    record.add_argument("--out", type=Path, required=True)
    record.add_argument("--registry", type=Path, default=None,
                        help="directorio del registro de autoría sobre el que se graba")
    record.add_argument("--catalog", type=Path, default=None, help="catálogo de datos de prueba")
    smoke = sub.add_parser("llm-smoke", help="prueba de humo del gateway de LLM (spec del gateway §7)")
    smoke.add_argument("--registry", type=Path, required=True, help="directorio del registro de autoría")
    smoke.add_argument("--profile", required=True, help="model_profile id@versión exacta")
    smoke.add_argument("--n", type=int, default=10, help="cantidad de llamadas (por defecto 10)")
    from agent_core.composition.registry import add_registry_parser, run_registry_cli

    add_registry_parser(sub)
    from agent_core.composition.serve import run_serve
    from agent_core.composition.serve_ports import add_serve_parser

    add_serve_parser(sub)
    from agent_core.composition.migrate import add_migrate_parser, run_migrate

    add_migrate_parser(sub)
    args = parser.parse_args(argv)
    if args.command == "llm-smoke":
        return _run_llm_smoke(args)
    if args.command == "registry":
        return run_registry_cli(args, clock=SystemClock(), ids=SystemIds(), env=os.environ.get)
    if args.command == "serve":
        return run_serve(args, clock=SystemClock(), ids=SystemIds(), env=os.environ)
    if args.command == "migrate":
        return run_migrate(args, os.environ)
    if args.command == "sweep":
        return _run_sweep(args, sweeper, clock)
    if args.command == "contracts":
        if args.check:
            diffs = sorted({*check_contracts(args.out), *check_openapi(args.out)})
            for rel in diffs:
                print(f"contracts desactualizado: {rel} (corre `uv run agentcore contracts`)",
                      file=sys.stderr)
            return 1 if diffs else 0
        write_contracts(args.out)
        write_openapi(args.out)
        return 0
    if args.command == "validate":
        clock = SystemClock()
        start = clock.monotonic_ns()
        code, output = run_validate(args.root, as_json=args.json)
        print(output)
        if not args.json:
            print(f"validado en {(clock.monotonic_ns() - start) // 1_000_000} ms", file=sys.stderr)
        return code
    if args.command == "replay":
        return _run_replay(args)
    if args.command == "record":
        return _run_record(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
