"""`serve` se configura solo con el entorno (brief A3): piezas reales por defecto, claves por variable."""

from pathlib import Path

import pytest

from agent_core.adapters.policy_authz import PolicyAuthz
from agent_core.adapters.tools.http_executor import HttpToolExecutor
from agent_core.composition.serve_ports import ServeConfigError
from agent_core.domain import GrantCheckUnavailable
from testing.fakes.clock import FakeClock
from testing.fakes.identity import TestIdentityIssuer
from tests.composition.test_serve_ports import _resolve
from tests.m09.test_identity_keys import _file


def _real_env(tmp: Path) -> dict[str, str]:
    for name in ("cal", "clf"):
        (tmp / name).mkdir(exist_ok=True)
    catalog = tmp / "catalog.json"
    catalog.write_text("{}", encoding="utf-8")
    keys = _file(tmp, TestIdentityIssuer(FakeClock()))
    return {"AGENTCORE_TOOL_SERVICE_URL": "http://tools.internal", "AGENTCORE_TOOL_SERVICE_TOKEN": "t",
            "AGENTCORE_GRANTS_URL": "http://127.0.0.1:1", "AGENTCORE_GRANTS_TOKEN": "g",
            "AGENTCORE_CALIBRATION_DIR": str(tmp / "cal"),
            "AGENTCORE_CLASSIFIER_ARTIFACTS_DIR": str(tmp / "clf"),
            "AGENTCORE_FIELD_CLASSIFICATION_FILES": str(catalog), "AGENTCORE_IDENTITY_KEYS_FILE": str(keys)}


@pytest.fixture
def real_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """El entorno real: las fábricas leen `os.environ`, así que se fija ahí y también se devuelve."""
    env = _real_env(tmp_path)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return env


def test_production_resolves_with_the_real_pieces_by_default_from_the_environment(
        real_env: dict[str, str]) -> None:
    ports = _resolve(**real_env)
    assert ports.doubles == ()
    assert isinstance(ports.tools, HttpToolExecutor) and isinstance(ports.authz, PolicyAuthz)
    assert type(ports.transcript).__name__ == "PgTranscriptStore"
    assert type(ports.calibrations).__name__ == "DirectoryCalibrationSource"
    assert type(ports.classifier).__name__ == "FieldClassifier"
    assert isinstance(ports.verifier._current(), object)  # type: ignore[attr-defined]


def test_without_the_identity_keys_the_startup_names_the_variable() -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve()
    assert "AGENTCORE_IDENTITY_KEYS_FILE" in " ".join(info.value.problems)


def test_without_the_piece_environment_every_missing_piece_names_its_variable(tmp_path: Path) -> None:
    keys_only = {"AGENTCORE_IDENTITY_KEYS_FILE": _real_env(tmp_path)["AGENTCORE_IDENTITY_KEYS_FILE"]}
    with pytest.raises(ServeConfigError) as info:
        _resolve(**keys_only)
    text = " ".join(info.value.problems)
    for variable in ("AGENTCORE_TOOL_SERVICE_URL", "AGENTCORE_GRANTS_URL", "AGENTCORE_CALIBRATION_DIR",
                     "AGENTCORE_CLASSIFIER_ARTIFACTS_DIR", "AGENTCORE_FIELD_CLASSIFICATION_FILES"):
        assert variable in text


def test_an_explicit_argument_still_wins_over_the_default(real_env: dict[str, str]) -> None:
    class_path = "tests.composition.test_serve_ports:real_piece"
    ports = _resolve("--authz", class_path, **real_env)
    assert type(ports.authz).__name__ == "_Piece"


def test_a_double_by_default_is_still_refused_in_production(real_env: dict[str, str]) -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve("--tools", "testing.serve_demo:tools", **real_env)
    assert "AGENTCORE_ALLOW_DOUBLES=1" in " ".join(info.value.problems)


def test_the_registry_api_comes_from_the_environment(real_env: dict[str, str]) -> None:
    env = real_env
    ports = _resolve(AGENTCORE_REGISTRY_API="1", AGENTCORE_EVAL_DSN="postgresql://ignored/eval",
                     AGENTCORE_STAFF_KEYS_FILE=env["AGENTCORE_IDENTITY_KEYS_FILE"], **env)
    assert ports.registry_api is not None and ports.run_export is not None


def test_the_registry_api_still_needs_the_staff_keys_and_the_eval_dsn(real_env: dict[str, str]) -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve(AGENTCORE_REGISTRY_API="1", **real_env)
    text = " ".join(info.value.problems)
    assert "AGENTCORE_EVAL_DSN" in text and "AGENTCORE_STAFF_KEYS_FILE" in text


def test_the_reload_interval_comes_from_the_environment(real_env: dict[str, str]) -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve(AGENTCORE_KEYS_RELOAD_SECONDS="-1", **real_env)
    assert "AGENTCORE_KEYS_RELOAD_SECONDS" in " ".join(info.value.problems)


def test_the_grant_check_is_the_http_one(real_env: dict[str, str]) -> None:
    ports = _resolve(**real_env)
    with pytest.raises(GrantCheckUnavailable):  # nothing listens on port 1: the HTTP client fails closed
        ports.verifier.grant_active("g-1", FakeClock().now())
