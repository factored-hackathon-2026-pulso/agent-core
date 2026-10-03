"""ADR 0022: la superficie de composición y los esquemas SQL que consume el repo de infraestructura.

Estas pruebas fijan lo que el ADR declara estable. Si una falla, el cambio es una ruptura: súbelo por el
ADR (y avisa a quien consume la superficie), no ajustes la prueba en silencio."""

import dataclasses
import inspect
import re
from pathlib import Path

import pytest

from agent_core.api.app import ApiDeps
from agent_core.composition.serve import build_api_deps
from agent_core.composition.serve_ports import ServePorts, resolve_ports
from agent_core.registry.evaluation.evaluator import ScenarioEvaluator
from agent_core.registry.http import registry_extension
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.postgres.store import PgRegistryStore
from agent_core.registry.service import RegistryService

ROOT = Path(__file__).resolve().parents[1] / "agent_core"


def _params(fn: object) -> list[str]:
    return list(inspect.signature(fn).parameters)  # type: ignore[arg-type]


def _fields(cls: type) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_resolve_ports_signature() -> None:
    assert _params(resolve_ports) == ["args", "env", "clock", "ids", "tracer"]


def test_build_api_deps_signature_only_grows_by_keyword() -> None:
    assert _params(build_api_deps)[:1] == ["ports"]
    assert {"registry_service", "telemetry"} <= set(_params(build_api_deps))


def test_registry_extension_and_service_signatures() -> None:
    assert _params(registry_extension)[:3] == ["service", "verifier", "clock"]
    assert _params(RegistryService.__init__)[:6] == ["self", "store", "evaluator", "clock", "ids", "runs"]
    assert {"limits", "quotas"} <= set(_params(RegistryService.__init__))


def test_api_deps_and_serve_ports_keep_their_fields() -> None:
    assert {"verifier", "registry", "clock", "ids", "turns", "extensions", "readiness"} <= _fields(ApiDeps)
    assert {"clock", "ids", "keys", "uow_factory", "audit", "registry", "verifier", "registry_api",
            "readiness"} <= _fields(ServePorts)


@pytest.mark.parametrize("cls", [ScenarioEvaluator, PgRegistryStore, InMemoryRegistryStore])
def test_the_adapters_the_infra_repo_imports_exist(cls: type) -> None:
    assert inspect.isclass(cls)


# --- migraciones expand/contract (N-11) ------------------------------------------------------------------

_SQL = sorted(ROOT.rglob("*.sql"))
_FORBIDDEN = [
    (r"\bDROP\s+(TABLE|COLUMN|INDEX|SCHEMA|CONSTRAINT)\b", "DROP de un objeto de datos"),
    (r"\bRENAME\b", "RENAME"),
    (r"\bALTER\s+COLUMN\b.*\b(TYPE|SET\s+NOT\s+NULL)\b", "ALTER COLUMN TYPE / SET NOT NULL"),
    (r"^TRUNCATE\b", "TRUNCATE"),
]


def _statements(text: str) -> list[str]:
    no_comments = re.sub(r"--[^\n]*", "", text)
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def test_there_are_sql_scripts_to_check() -> None:
    assert len(_SQL) >= 3


@pytest.mark.parametrize("path", _SQL, ids=lambda p: p.name)
def test_sql_scripts_only_expand(path: Path) -> None:
    for statement in _statements(path.read_text(encoding="utf-8")):
        flat = " ".join(statement.split())
        for pattern, label in _FORBIDDEN:
            assert not re.search(pattern, flat, re.IGNORECASE), f"{path.name}: {label}: {flat[:80]}"
        add = re.search(r"\bADD\s+COLUMN\b(?P<rest>.*)", flat, re.IGNORECASE)
        if add:
            rest = add["rest"]
            assert "IF NOT EXISTS" in rest.upper(), f"{path.name}: ADD COLUMN sin IF NOT EXISTS"
            if "NOT NULL" in rest.upper():
                assert "DEFAULT" in rest.upper(), f"{path.name}: ADD COLUMN NOT NULL sin DEFAULT"
