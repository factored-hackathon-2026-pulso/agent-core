"""Puertos de `agentcore serve`: piezas reales por defecto; dobles por ruta solo con ALLOW_DOUBLES=1."""

import argparse
import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psycopg
from opentelemetry.trace import Tracer
from psycopg_pool import ConnectionPool

from agent_core.adapters.env_keys import EnvKeyProvider, KeyConfigError
from agent_core.adapters.identity_keys import ReloadingIdentityVerifier
from agent_core.adapters.llm import HttpLLMGateway, UnconfiguredLLMGateway
from agent_core.adapters.llm.http_gateway import LLM_GATEWAY_TOKEN_ENV, LLM_GATEWAY_URL_ENV
from agent_core.adapters.postgres_uow import PostgresStore
from agent_core.composition.blobs import blob_factory_from_env
from agent_core.composition.engine_tools import fx_rates_from_env
from agent_core.decision import DecisionConfigError, DecisionProvider, HttpJevTransport, JevProvider
from agent_core.decision.calibration.artifact import CalibrationSource
from agent_core.domain import Release, SchemaError, loads
from agent_core.guards import LangThresholds
from agent_core.ports import (
    AgentDirectory,
    AuditSink,
    AuthzPort,
    Clock,
    CostCounters,
    IdentityVerifier,
    IdSource,
    KeyProvider,
    LLMGateway,
    RegistryPort,
    RunExport,
    ToolExecutor,
    TranscriptStore,
    UnitOfWorkFactory,
)
from agent_core.registry import PgRegistryStore, PostgresRegistry, RegistryDirectory, RegistryStore
from agent_core.views import FieldClassifier

DOUBLES_ENV = "AGENTCORE_ALLOW_DOUBLES"
LEGACY_DOUBLES_ENV = "AGENTCORE_ALLOW_DEMO"  # nombre anterior del mismo interruptor; sigue valiendo
JEV_KEY_ENV = "AGENTCORE_JEV_API_KEY"
DSN_ENV = "AGENTCORE_REGISTRY_DSN"
EVAL_DSN_ENV = "AGENTCORE_EVAL_DSN"
AGENTS_ENV = "AGENTCORE_SERVE_AGENTS"
LANG_THRESHOLDS_ENV = "AGENTCORE_LANG_THRESHOLDS"  # archivo JSON con los umbrales de idioma (M6 §3.1.7)
POOL_MAX_ENV = "AGENTCORE_DB_POOL_MAX"  # conexiones máximas por proceso; 0 o ausente = una por operación
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


def doubles_allowed(env: Mapping[str, str]) -> bool:
    """`True` si el entorno permite dobles de demo: `AGENTCORE_ALLOW_DOUBLES=1` o el nombre heredado."""
    return env.get(DOUBLES_ENV) == "1" or env.get(LEGACY_DOUBLES_ENV) == "1"


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
    store: PostgresStore | None = None  # la base del motor, con su pool: las piezas persistentes la comparten


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
    agents: tuple[str, ...] = ()  # agentes cuya release `prod` se revisa al arrancar (aviso)
    llm_gateway_url: str | None = None  # None: sin llm-gateway configurado (toda generación cae a plantilla)
    endpoints: Mapping[str, Any] = field(default_factory=dict)  # deprecated, always empty (ADR 0022)
    registry_api: RegistryApiPorts | None = None  # solo con --registry-api
    directory: AgentDirectory | None = None  # ADR 0021: directorio de especialistas (sobre el registry)
    readiness: tuple[tuple[str, Callable[[], bool]], ...] = ()  # comprobaciones de `/readyz`
    run_export: RunExport | None = None  # N-08: lectura paginada de runs y eventos (con --registry-api)
    lang_thresholds: Mapping[str, LangThresholds] = field(default_factory=dict)  # por `thresholds_from`
    # Tabla FIJA de tasas de `convertir_moneda` (AGENTCORE_FX_RATES_FILE); None: la tool falla cerrada.
    fx_rates: Mapping[str, Decimal] | None = None
    # El motor sirve sus tools (`composition.engine_tools`): solo fuera de demo, lo decide `resolve_ports`.
    engine_tools: bool = False


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
    serve.add_argument("--keys-reload-seconds", type=float, default=5.0,
                       help="cada cuántos segundos, a lo sumo, se vuelve a leer --identity-keys y "
                            "--staff-keys (rotar sin reiniciar; una lectura rota conserva las últimas "
                            "claves buenas); 0 lo apaga")
    serve.add_argument("--agents", default=None,
                       help=f"agentes separados por coma (o {AGENTS_ENV}): al arrancar avisa de los perfiles "
                            "de "
                            "su release `prod` cuyo alias de LLM no esté configurado")
    serve.add_argument("--lang-thresholds", type=Path, default=None,
                       help=f"archivo JSON {{thresholds_from: {{switch_threshold, unsupported_threshold, "
                            f"min_distance}}}} (o {LANG_THRESHOLDS_ENV}). Sin él el idioma del run nunca "
                            "cambia por detección (umbral 1.0 = desactivado, M6 §3.1.7)")
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
    """Las rutas bajo `testing` son dobles de prueba: no se aceptan sin AGENTCORE_ALLOW_DOUBLES=1."""
    module = path.partition(":")[0]
    return module == "testing" or module.startswith("testing.")


def _jev_key(env: Mapping[str, str]) -> str:
    key = env.get(JEV_KEY_ENV)
    if not key:
        raise DecisionConfigError(f"falta {JEV_KEY_ENV}: no se puede llamar a JEV sin clave")
    return key


def _registry_connect(dsn: str, pool_max: int) -> Callable[[], Any]:
    """Conexión transaccional del registry: del pool propio (autocommit apagado) o una por transacción."""
    if pool_max <= 0:
        return lambda: psycopg.connect(dsn, autocommit=False)
    pool: ConnectionPool[Any] = ConnectionPool(dsn, min_size=1, max_size=pool_max, open=False,
                                               check=ConnectionPool.check_connection)
    opened: list[bool] = []

    def connection() -> Any:
        if not opened:
            pool.open()
            opened.append(True)
        return pool.connection()  # contexto: commit al salir sin error, rollback con excepción, y devuelve

    return connection


def _llm_gateway_config(env: Mapping[str, str], problems: list[str]) -> tuple[str | None, str | None]:
    """The llm-gateway URL and token, both or neither. Neither is allowed (generation falls back to templates)
    and announced at startup; half of the pair or an invalid URL is a configuration problem."""
    url = (env.get(LLM_GATEWAY_URL_ENV) or "").strip()
    token = (env.get(LLM_GATEWAY_TOKEN_ENV) or "").strip()
    if not url and not token:
        return None, None
    if not url or not token:
        problems.append(f"{LLM_GATEWAY_URL_ENV} y {LLM_GATEWAY_TOKEN_ENV} van juntas: falta una de las dos")
        return None, None
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        problems.append(f"{LLM_GATEWAY_URL_ENV} debe ser una URL http(s) con host")
        return None, None
    return url, token


def _lang_thresholds(args: argparse.Namespace, env: Mapping[str, str],
                     problems: list[str]) -> dict[str, LangThresholds]:
    """Umbrales de idioma por `thresholds_from`, de un archivo JSON. Un archivo roto es un problema de
    configuración: callar dejaría el cambio de idioma apagado sin que nadie lo note."""
    raw = args.lang_thresholds or (Path(env[LANG_THRESHOLDS_ENV]) if env.get(LANG_THRESHOLDS_ENV) else None)
    if raw is None:
        return {}
    try:
        data = loads(Path(raw).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("debe ser un objeto {thresholds_from: umbrales}")
        return {str(name): LangThresholds.model_validate(value) for name, value in data.items()}
    except (OSError, ValueError) as exc:  # ValidationError es un ValueError; sin el texto del archivo
        problems.append(f"--lang-thresholds: {type(exc).__name__}: archivo ilegible o umbrales inválidos")
        return {}


def _agents(args: argparse.Namespace, env: Mapping[str, str]) -> tuple[str, ...]:
    raw = args.agents or env.get(AGENTS_ENV) or ""
    return tuple(a for a in (part.strip() for part in raw.split(",")) if a)


def resolve_ports(args: argparse.Namespace, env: Mapping[str, str],
                  clock: Clock, ids: IdSource, *, tracer: Tracer | None = None) -> ServePorts:
    """`tracer` is kept for the stable composition surface (ADR 0022) and is unused: the `chat` span is
    emitted by the llm-gateway service (ADR 0024)."""
    problems: list[str] = []
    demo = doubles_allowed(env)

    dsn = args.dsn or env.get(DSN_ENV)
    if not dsn:
        problems.append(f"falta --dsn (o {DSN_ENV})")
    pool_max = 0
    try:
        pool_max = int(env.get(POOL_MAX_ENV) or 0)
        if pool_max < 0:
            raise ValueError
    except ValueError:
        problems.append(f"{POOL_MAX_ENV} debe ser un entero >= 0")
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
                            f"los dobles de demo solo se usan con {DOUBLES_ENV}=1")
        elif _is_test_double(given):
            problems.append(f"{name}: `{given}` es un doble de prueba; solo se usa con {DOUBLES_ENV}=1")
        else:
            chosen[name] = given
    if not demo and args.identity_keys is None:
        problems.append("falta --identity-keys (archivo de claves públicas de identidad)")

    # Configuración estática: se valida entera antes de ejecutar cualquier fábrica de pieza.
    reload_seconds = getattr(args, "keys_reload_seconds", 5.0)
    if reload_seconds < 0:
        problems.append("--keys-reload-seconds no puede ser negativo")
    reload_every = timedelta(seconds=max(reload_seconds, 0.0))
    grant_active: list[Callable[[str, datetime], bool]] = []  # lo llena la fábrica de `grant-active`
    verifier: IdentityVerifier | None = None
    if args.identity_keys is not None:
        try:
            verifier = ReloadingIdentityVerifier(
                args.identity_keys, lambda ref, now: grant_active[0](ref, now), clock, reload_every)
        except SchemaError as exc:
            problems.append(str(exc))
    llm_url, llm_token = _llm_gateway_config(env, problems)
    lang_thresholds = _lang_thresholds(args, env, problems)
    fx_rates: dict[str, Decimal] | None = None
    try:
        fx_rates = fx_rates_from_env(env)
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
                staff_verifier = ReloadingIdentityVerifier(
                    args.staff_keys, lambda ref, now: False, clock, reload_every, delegation=False)
            except SchemaError as exc:
                problems.append(f"--staff-keys: {exc}")
        elif not demo:
            problems.append("falta --staff-keys (archivo de claves públicas del emisor del staff)")
    if problems or not dsn or keys is None:
        raise ServeConfigError(problems)

    store = PostgresStore(dsn, pool_max=pool_max)
    registry_store = PgRegistryStore(_registry_connect(dsn, pool_max), blob_factory_from_env(env))
    pg_registry = PostgresRegistry(registry_store, clock)
    ctx = DemoContext(clock=clock, ids=ids, registry=pg_registry, store=store)
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

    doubles = [name for name, path in chosen.items() if _is_test_double(path)]
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
        eval_store = PostgresStore(eval_dsn, pool_max=pool_max)
        registry_api = RegistryApiPorts(store=registry_store, staff_verifier=staff_verifier,
                                        eval_uow_factory=eval_store.uow, eval_audit=eval_store.audit())

    gateway: LLMGateway = (HttpLLMGateway(pg_registry, llm_url, llm_token)
                           if llm_url is not None and llm_token is not None else UnconfiguredLLMGateway())
    jev = JevProvider(HttpJevTransport(lambda: _jev_key(env), clock))
    return ServePorts(
        clock=clock, ids=ids, keys=keys, uow_factory=store.uow, audit=store.audit(), counters=store.costs(),
        registry=pg_registry, releases=pg_registry.release, gateway=gateway,
        providers={"jev": jev, "classifier": built["classifier"]},
        tools=built["tools"], authz=built["authz"], transcript=built["transcript"],
        calibrations=built["calibration"], classifier=built["field-classifier"], verifier=verifier,
        doubles=tuple(doubles), agents=_agents(args, env), llm_gateway_url=llm_url,
        registry_api=registry_api,
        directory=RegistryDirectory(registry_store, pg_registry, pg_registry.release),
        readiness=(("postgres", store.ping),), run_export=store.run_export(), lang_thresholds=lang_thresholds,
        fx_rates=fx_rates, engine_tools=not demo)
