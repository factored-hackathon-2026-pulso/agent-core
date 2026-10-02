"""Puertos de `agentcore serve`: piezas reales por defecto; dobles por ruta solo con ALLOW_DEMO=1."""

import argparse
import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from opentelemetry.trace import Tracer

from agent_core.adapters.env_keys import EnvKeyProvider, KeyConfigError
from agent_core.adapters.identity_keys import load_identity_verifier
from agent_core.adapters.llm import EndpointConfig, OpenAICompatGateway, load_endpoints
from agent_core.adapters.postgres_uow import PostgresStore
from agent_core.decision import DecisionConfigError, DecisionProvider, HttpJevTransport, JevProvider
from agent_core.decision.calibration.artifact import CalibrationSource
from agent_core.domain import Release, SchemaError
from agent_core.ports import (
    AuditSink,
    AuthzPort,
    Clock,
    CostCounters,
    IdentityVerifier,
    IdSource,
    KeyProvider,
    LLMGateway,
    RegistryPort,
    ToolExecutor,
    TranscriptStore,
    UnitOfWorkFactory,
)
from agent_core.registry import PgRegistryStore, PostgresRegistry, RegistryStore
from agent_core.views import FieldClassifier

DEMO_ENV = "AGENTCORE_ALLOW_DEMO"
JEV_KEY_ENV = "AGENTCORE_JEV_API_KEY"
DSN_ENV = "AGENTCORE_REGISTRY_DSN"
EVAL_DSN_ENV = "AGENTCORE_EVAL_DSN"
AGENTS_ENV = "AGENTCORE_SERVE_AGENTS"
DEMO_VERIFIER = "testing.registry_demo:demo_verifier"
_KEY_VARS = ("AGENTCORE_KEYS_FINGERPRINT", "AGENTCORE_KEYS_TOKEN_MAP")

# (atributo de args, nombre de la pieza, ruta por defecto en demo)
_DOUBLES: tuple[tuple[str, str, str], ...] = (
    ("tools", "tools", "testing.serve_demo:tools"),
    ("authz", "authz", "testing.serve_demo:authz"),
    ("transcript", "transcript", "testing.serve_demo:transcript"),
    ("calibration", "calibration", "testing.serve_demo:calibration"),
    ("classifier", "classifier", "testing.serve_demo:classifier_provider"),
    ("field_classifier", "field-classifier", "testing.serve_demo:field_classifier"),
    ("grant_active", "grant-active", "testing.serve_demo:grant_active"),
)


class ServeConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class DemoContext:
    """Lo que reciben las fábricas de piezas (`modulo:atributo`) para construirse."""

    clock: Clock
    ids: IdSource
    registry: RegistryPort


@dataclass(frozen=True)
class RegistryApiPorts:
    """Lo que `--registry-api` suma a `serve`: el almacén del registry, el verificador del staff y la base
    propia de las evaluaciones (para no mezclar sus runs con los de producción)."""

    store: RegistryStore
    staff_verifier: IdentityVerifier
    eval_uow_factory: UnitOfWorkFactory
    eval_audit: AuditSink


@dataclass(frozen=True)
class ServePorts:
    clock: Clock
    ids: IdSource
    keys: KeyProvider
    uow_factory: UnitOfWorkFactory
    audit: AuditSink
    counters: CostCounters
    registry: RegistryPort
    releases: Callable[[str], Release]
    gateway: LLMGateway
    providers: Mapping[str, DecisionProvider]
    tools: ToolExecutor
    authz: AuthzPort
    transcript: TranscriptStore
    calibrations: CalibrationSource
    classifier: FieldClassifier | None
    verifier: IdentityVerifier
    doubles: tuple[str, ...]  # piezas que son dobles de demo (vacío = todo real)
    agents: tuple[str, ...] = ()  # agentes cuya release `prod` se revisa al arrancar (aviso de alias)
    endpoints: Mapping[str, EndpointConfig] = field(default_factory=dict)
    registry_api: RegistryApiPorts | None = None  # solo con --registry-api


def _flag(attr: str) -> str:
    return "--" + attr.replace("_", "-")


def add_serve_parser(sub: Any) -> None:
    serve = sub.add_parser("serve", help="arranca el servidor HTTP (M9) con el motor compuesto")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--dsn", default=None,
                       help=f"DSN de Postgres; prefiere {DSN_ENV}: por argv la contraseña queda visible "
                            "en la lista de procesos. No se imprime nunca")
    serve.add_argument("--identity-keys", type=Path, default=None,
                       help="archivo con las claves públicas de identidad (principal y delegación)")
    serve.add_argument("--agents", default=None,
                       help=f"agentes separados por coma (o {AGENTS_ENV}): al arrancar avisa de los perfiles "
                            "de "
                            "su release `prod` cuyo alias de LLM no esté configurado")
    serve.add_argument("--registry-api", action="store_true",
                       help="monta la API HTTP del registry (/v1/registry); exige --eval-dsn y --staff-keys")
    serve.add_argument("--eval-dsn", default=None,
                       help=f"DSN de la base propia de las evaluaciones del registry; prefiere "
                            f"{EVAL_DSN_ENV} "
                            "(por argv queda visible en la lista de procesos). No se imprime nunca")
    serve.add_argument("--staff-keys", type=Path, default=None,
                       help="archivo con las claves públicas del emisor del staff (mismo formato que "
                            "--identity-keys; `delegation_keys` es opcional)")
    for attr, name, default in _DOUBLES:
        serve.add_argument(_flag(attr), default=None, dest=attr,
                           help=f"{name} (modulo:atributo); en demo: {default}")


def _load(path: str) -> Any:
    module, sep, attr = path.partition(":")
    if not sep or not attr:
        raise ValueError(f"`{path}` no tiene la forma modulo:atributo")
    return getattr(importlib.import_module(module), attr)


def _is_test_double(path: str) -> bool:
    """Las rutas bajo `testing` son dobles de prueba: no se aceptan sin AGENTCORE_ALLOW_DEMO=1."""
    module = path.partition(":")[0]
    return module == "testing" or module.startswith("testing.")


def _jev_key(env: Mapping[str, str]) -> str:
    key = env.get(JEV_KEY_ENV)
    if not key:
        raise DecisionConfigError(f"falta {JEV_KEY_ENV}: no se puede llamar a JEV sin clave")
    return key


def _agents(args: argparse.Namespace, env: Mapping[str, str]) -> tuple[str, ...]:
    raw = args.agents or env.get(AGENTS_ENV) or ""
    return tuple(a for a in (part.strip() for part in raw.split(",")) if a)


def resolve_ports(args: argparse.Namespace, env: Mapping[str, str],
                  clock: Clock, ids: IdSource, *, tracer: Tracer | None = None) -> ServePorts:
    problems: list[str] = []
    demo = env.get(DEMO_ENV) == "1"

    dsn = args.dsn or env.get(DSN_ENV)
    if not dsn:
        problems.append(f"falta --dsn (o {DSN_ENV})")
    keys: KeyProvider | None
    try:
        keys = EnvKeyProvider({k: env[k] for k in _KEY_VARS if env.get(k)})
    except KeyConfigError as exc:
        keys = None
        problems.append(str(exc))

    chosen: dict[str, str] = {}
    for attr, name, default in _DOUBLES:
        given = getattr(args, attr)
        if demo:
            chosen[name] = given or default
        elif given is None:
            problems.append(f"falta {name}: pasa {_flag(attr)} con una pieza real; "
                            f"los dobles de demo solo se usan con {DEMO_ENV}=1")
        elif _is_test_double(given):
            problems.append(f"{name}: `{given}` es un doble de prueba; solo se usa con {DEMO_ENV}=1")
        else:
            chosen[name] = given
    if not demo and args.identity_keys is None:
        problems.append("falta --identity-keys (archivo de claves públicas de identidad)")

    # Configuración estática: se valida entera antes de ejecutar cualquier fábrica de pieza.
    grant_active: list[Callable[[str, datetime], bool]] = []  # lo llena la fábrica de `grant-active`
    verifier: IdentityVerifier | None = None
    if args.identity_keys is not None:
        try:
            verifier = load_identity_verifier(args.identity_keys, lambda ref, now: grant_active[0](ref, now))
        except SchemaError as exc:
            problems.append(str(exc))
    endpoints: dict[str, EndpointConfig] = {}
    try:
        endpoints = load_endpoints(env)
    except SchemaError as exc:
        problems.append(str(exc))
    eval_dsn: str | None = None
    staff_verifier: IdentityVerifier | None = None
    if args.registry_api:
        eval_dsn = args.eval_dsn or env.get(EVAL_DSN_ENV)
        if not eval_dsn:
            problems.append(f"falta --eval-dsn (o {EVAL_DSN_ENV}): las evaluaciones del registry usan "
                            "su propia base")
        elif eval_dsn == dsn:
            problems.append("--eval-dsn debe ser distinto de --dsn: las evaluaciones no pueden "
                            "escribir en la base de producción")
        if args.staff_keys is not None:
            try:
                staff_verifier = load_identity_verifier(args.staff_keys, lambda ref, now: False,
                                                        delegation=False)
            except SchemaError as exc:
                problems.append(f"--staff-keys: {exc}")
        elif not demo:
            problems.append("falta --staff-keys (archivo de claves públicas del emisor del staff)")
    if problems or not dsn or keys is None:
        raise ServeConfigError(problems)

    store = PostgresStore(dsn)
    registry_store = PgRegistryStore(lambda: psycopg.connect(dsn, autocommit=False))
    pg_registry = PostgresRegistry(registry_store, clock)
    ctx = DemoContext(clock=clock, ids=ids, registry=pg_registry)
    built: dict[str, Any] = {}
    for name, path in chosen.items():
        try:
            piece = _load(path)(ctx)
        except Exception as exc:  # código de usuario: cualquier fallo es un problema de configuración
            problems.append(f"no se pudo cargar {name} ({path}): {type(exc).__name__}; "
                            "revisa la ruta modulo:atributo y que la fábrica acepte un DemoContext")
            continue
        if piece is None:
            problems.append(f"{name} ({path}): la fábrica no devolvió ninguna pieza")
            continue
        built[name] = piece
    if problems:
        raise ServeConfigError(problems)

    doubles = list(built) if demo else []
    grant_active.append(built["grant-active"])
    if verifier is None:  # sin archivo de claves solo se llega aquí en demo (arriba se exigió lo contrario)
        verifier = _load(DEMO_VERIFIER)()
        doubles.append("identity")

    registry_api: RegistryApiPorts | None = None
    if args.registry_api:
        assert eval_dsn is not None
        if staff_verifier is None:  # solo en demo (arriba se exigió --staff-keys fuera de demo)
            staff_verifier = _load(DEMO_VERIFIER)()
            doubles.append("staff-identity")
        eval_store = PostgresStore(eval_dsn)
        registry_api = RegistryApiPorts(store=registry_store, staff_verifier=staff_verifier,
                                        eval_uow_factory=eval_store.uow, eval_audit=eval_store.audit())

    gateway = OpenAICompatGateway(pg_registry, endpoints, env, tracer=tracer)
    jev = JevProvider(HttpJevTransport(lambda: _jev_key(env), clock))
    return ServePorts(
        clock=clock, ids=ids, keys=keys, uow_factory=store.uow, audit=store.audit(), counters=store.costs(),
        registry=pg_registry, releases=pg_registry.release, gateway=gateway,
        providers={"jev": jev, "classifier": built["classifier"]},
        tools=built["tools"], authz=built["authz"], transcript=built["transcript"],
        calibrations=built["calibration"], classifier=built["field-classifier"], verifier=verifier,
        doubles=tuple(doubles), agents=_agents(args, env), endpoints=endpoints,
        registry_api=registry_api)
