"""CLI `agentcore`: `contracts`, `validate`, `replay` y `record`."""

import argparse
import importlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import psycopg

from agent_core.actions import ActionManager
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
    load_catalog,
    load_fixture_file,
)
from agent_core.audit.replay.cli_support import EXIT, USAGE_ERROR, render_report
from agent_core.contracts import check_contracts, write_contracts
from agent_core.domain import (
    AgentSelector,
    EntityKind,
    EntityRef,
    Principal,
    RegistryEntity,
    Release,
    SchemaError,
)
from agent_core.flows import AuthoringRegistry, load_registry
from agent_core.flows.cli_validate import run_validate
from agent_core.ports import Clock
from agent_core.turn import Sweeper, SweepReport

ENGINE_UNAVAILABLE_MESSAGE = "motor M4/M2 no disponible: el replay necesita el motor integrado"
RECORD_UNAVAILABLE_MESSAGE = "motor M4/M2 no disponible: record necesita el motor integrado"
_TURN_MODULE = "agent_core.turn"
DSN_ENV = "AGENTCORE_DATABASE_URL"


class EngineUnavailable(RuntimeError):
    """El motor integrado (M4 + M2 + M5/M6/M8) no está cableado todavía (Task 14)."""


def load_engine() -> EngineRunner:
    """Construye el `EngineRunner` con el motor real. Mientras M4 no exista lanza `EngineUnavailable`."""
    try:
        turn = importlib.import_module(_TURN_MODULE)
    except ModuleNotFoundError as error:
        if error.name != _TURN_MODULE:  # un fallo interno del motor no es "motor no disponible"
            raise
        raise EngineUnavailable from error
    builder = getattr(turn, "build_engine_runner", None)
    if builder is None:
        raise EngineUnavailable
    runner: EngineRunner = builder()
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
        engine = load_engine()
    except EngineUnavailable:
        print(ENGINE_UNAVAILABLE_MESSAGE, file=sys.stderr)
        return USAGE_ERROR
    try:
        result = Replayer(engine, clock).replay(fixture, args.mode)
    except Exception as error:
        print(f"replay falló: {type(error).__name__}", file=sys.stderr)
        return USAGE_ERROR
    print(render_report(result, args.json))
    return EXIT[result.verdict]


def _run_record(_: argparse.Namespace) -> int:
    try:
        load_engine()
    except EngineUnavailable:
        print(RECORD_UNAVAILABLE_MESSAGE, file=sys.stderr)
        return USAGE_ERROR
    print("record: implementación real pendiente (Task 14)", file=sys.stderr)
    return USAGE_ERROR


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
    record = sub.add_parser("record", help="graba un fixture de un camino (M11)")
    record.add_argument("scenario")
    record.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
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
