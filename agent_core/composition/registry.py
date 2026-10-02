"""Cableado del registry: servicio sobre Postgres, lector de runs y subcomando `agentcore registry`."""

import argparse
import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
from pydantic import ValidationError

from agent_core.domain import Principal, dumps, loads
from agent_core.flows import load_yaml
from agent_core.ports import Clock, IdentityVerifier, IdSource, UnitOfWorkFactory
from agent_core.registry import (
    EntityDraft,
    EvalPort,
    LocalSandbox,
    Origin,
    PgRegistryStore,
    RegistryError,
    RegistryErrorCode,
    RegistryService,
    RegistryStore,
    RunReleaseReader,
    ScenarioEvaluator,
)


class UowRunReleases:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def release_of(self, run_id: str) -> str | None:
        with self._uow_factory() as uow:
            run = uow.load_run(run_id)
        return run.release if run is not None else None


def build_registry_service(dsn: str, *, evaluator: EvalPort, clock: Clock, ids: IdSource,
                           runs: RunReleaseReader | None = None) -> RegistryService:
    store = PgRegistryStore(lambda: psycopg.connect(dsn, autocommit=False))
    return RegistryService(store, evaluator, clock, ids, runs=runs)


# Dobles de PRUEBA (claves y mundo sintéticos en el repo): solo se usan si AGENTCORE_ALLOW_DEMO=1.
DEMO_VERIFIER = "testing.registry_demo:demo_verifier"
DEMO_HARNESS = "testing.registry_demo:build_harness"


def _load(path: str) -> Any:
    module, sep, attr = path.partition(":")
    if not sep or not attr:
        raise ValueError(f"`{path}` no tiene la forma modulo:atributo")
    return getattr(importlib.import_module(module), attr)


def _drafts(paths: list[Path]) -> list[EntityDraft]:
    """Cada archivo YAML: `kind`, `docs` y `content` (la entidad en el formato de M1)."""
    out = []
    for path in paths:
        files = sorted(path.rglob("*.yaml")) if path.is_dir() else [path]
        for f in files:
            data = load_yaml(f.read_bytes())
            if not isinstance(data, dict):
                raise RegistryError(RegistryErrorCode.validation_failed,
                                    f"{f}: el archivo debe ser un mapa con `kind`, `docs` y `content`")
            try:
                out.append(EntityDraft.model_validate(data))
            except ValidationError as exc:  # sin `input`: el contenido puede traer texto libre
                fields = sorted({".".join(str(x) for x in e["loc"]) for e in exc.errors(include_input=False)})
                raise RegistryError(RegistryErrorCode.validation_failed,
                                    f"{f}: no cumple el formato de draft ({', '.join(fields)})") from None
    return out


def add_registry_parser(sub: Any) -> None:
    reg = sub.add_parser("registry", help="registry de entidades (unidad 2)")
    reg.add_argument("--dsn", default=None, help="Postgres del registry (o AGENTCORE_REGISTRY_DSN)")
    reg.add_argument("--credential", default=None, help="JWS del principal (o AGENTCORE_CREDENTIAL)")
    reg.add_argument("--verifier", default=None,
                     help="verificador (modulo:atributo); obligatorio salvo AGENTCORE_ALLOW_DEMO=1")
    reg.add_argument("--harness", default=None,
                     help="harness de evaluación (modulo:atributo); obligatorio salvo AGENTCORE_ALLOW_DEMO=1")
    cmds = reg.add_subparsers(dest="registry_cmd", required=True)
    cmds.add_parser("import").add_argument("root", type=Path)
    exp = cmds.add_parser("export")
    exp.add_argument("release_id")
    exp.add_argument("out", type=Path)
    prop = cmds.add_parser("propose")
    prop.add_argument("agent_id")
    prop.add_argument("title")
    prop.add_argument("--origin", default="manual", choices=[o.value for o in Origin])
    draft = cmds.add_parser("draft")
    draft.add_argument("proposal_id")
    draft.add_argument("paths", type=Path, nargs="+")
    draft.add_argument("--rev", type=int, required=True)
    for name in ("validate", "freeze", "reopen", "show"):
        cmds.add_parser(name).add_argument("proposal_id")
    ev = cmds.add_parser("evaluate")
    ev.add_argument("proposal_id")
    ev.add_argument("suite_id")
    ap = cmds.add_parser("approve")
    ap.add_argument("proposal_id")
    ap.add_argument("candidate_hash")
    rj = cmds.add_parser("reject")
    rj.add_argument("proposal_id")
    rj.add_argument("reason")
    pb = cmds.add_parser("publish")
    pb.add_argument("proposal_id")
    pb.add_argument("idempotency_key")
    pr = cmds.add_parser("promote")
    pr.add_argument("agent_id")
    pr.add_argument("alias")
    pr.add_argument("release_id")
    rv = cmds.add_parser("revoke")
    rv.add_argument("release_id")
    rv.add_argument("reason")
    df = cmds.add_parser("diff")
    df.add_argument("a")
    df.add_argument("b")
    cmds.add_parser("lineage").add_argument("run_id")


def run_registry_cli(args: argparse.Namespace, *, clock: Clock, ids: IdSource,
                     env: Callable[[str], str | None], store: RegistryStore | None = None) -> int:
    """`store` permite inyectar el almacén (pruebas); sin él se usa Postgres con `--dsn`."""
    dsn = args.dsn or env("AGENTCORE_REGISTRY_DSN")
    credential = args.credential or env("AGENTCORE_CREDENTIAL")
    if (not dsn and store is None) or not credential:
        print("agentcore registry necesita --dsn y --credential (o sus variables de entorno)",
              file=sys.stderr)
        return 2
    demo = env("AGENTCORE_ALLOW_DEMO") == "1"
    verifier_path = args.verifier or (DEMO_VERIFIER if demo else None)
    harness_path = args.harness or (DEMO_HARNESS if demo else None)
    if verifier_path is None or harness_path is None:
        print("agentcore registry necesita --verifier y --harness (rutas modulo:atributo). Los dobles de "
              "prueba de testing.registry_demo solo se usan con AGENTCORE_ALLOW_DEMO=1", file=sys.stderr)
        return 2
    try:
        verifier: IdentityVerifier = _load(verifier_path)()
        harness = _load(harness_path)()
    except (ImportError, AttributeError, ValueError) as exc:
        print(f"no se pudo cargar el verificador o el harness ({type(exc).__name__}); "
              "revisa las rutas modulo:atributo y que el módulo esté instalado", file=sys.stderr)
        return 2
    actor: Principal = verifier.verify(credential)
    # max_workers=1: el harness comparte reloj e ids (no thread-safe) entre corridas.
    evaluator = ScenarioEvaluator(harness, LocalSandbox(ids), max_workers=1)
    if store is not None:
        service = RegistryService(store, evaluator, clock, ids)
    else:
        assert dsn is not None
        service = build_registry_service(dsn, evaluator=evaluator, clock=clock, ids=ids)
    try:
        result = _dispatch(service, actor, args)
    except RegistryError as exc:
        print(json.dumps({"code": exc.code.value, "detail": exc.detail, "payload": loads(dumps(exc.payload))},
                         ensure_ascii=False, indent=2, default=str), file=sys.stderr)
        return 1
    print(dumps(result) if result is not None else "ok")
    return 0


def _dispatch(s: RegistryService, actor: Principal, a: argparse.Namespace) -> Any:
    match a.registry_cmd:
        case "import":
            return s.import_seed(actor, a.root)
        case "export":
            for rel, data in s.export(a.release_id).items():
                (a.out / rel).parent.mkdir(parents=True, exist_ok=True)
                (a.out / rel).write_bytes(data)
            return None
        case "propose":
            return s.create_proposal(actor, a.agent_id, Origin(a.origin), a.title)
        case "draft":
            return s.put_draft(actor, a.proposal_id, _drafts(a.paths), a.rev)
        case "validate":
            return s.validate(actor, a.proposal_id)
        case "freeze":
            return s.freeze(actor, a.proposal_id)
        case "reopen":
            return s.reopen(actor, a.proposal_id)
        case "show":
            return s.get_proposal(a.proposal_id)
        case "evaluate":
            return s.evaluate(actor, a.proposal_id, a.suite_id)
        case "approve":
            return s.approve(actor, a.proposal_id, a.candidate_hash)
        case "reject":
            return s.reject(actor, a.proposal_id, a.reason)
        case "publish":
            return s.publish(actor, a.proposal_id, a.idempotency_key)
        case "promote":
            return s.promote(actor, a.agent_id, a.alias, a.release_id)
        case "revoke":
            return s.revoke(actor, a.release_id, a.reason)
        case "diff":
            return s.diff_releases(a.a, a.b)
        case "lineage":
            return s.lineage_for_run(actor, a.run_id)
    raise AssertionError(a.registry_cmd)
