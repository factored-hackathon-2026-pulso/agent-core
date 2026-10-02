"""Puertos de `agentcore serve`: piezas reales por defecto; dobles solo con AGENTCORE_ALLOW_DEMO=1."""

import argparse
import base64

import pytest

from agent_core.composition.serve_ports import ServeConfigError, ServePorts, add_serve_parser, resolve_ports
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds

DOUBLE_OPTIONS = ("--tools", "--authz", "--transcript", "--calibration", "--classifier",
                  "--field-classifier", "--grant-active")


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_serve_parser(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(["serve", *argv])


def _key(seed: bytes) -> str:
    return "k1:" + base64.b64encode(seed * 32).decode()


def _env(**extra: str) -> dict[str, str]:
    return {"AGENTCORE_KEYS_FINGERPRINT": _key(b"a"), "AGENTCORE_KEYS_TOKEN_MAP": _key(b"b"),
            "AGENTCORE_REGISTRY_DSN": "postgresql://ignored/ignored", **extra}


def _resolve(*argv: str, **env: str) -> ServePorts:
    return resolve_ports(_args(*argv), _env(**env), FakeClock(), FakeIds())


@pytest.mark.parametrize("value", [None, "0", "true", ""])
def test_without_the_demo_switch_every_missing_piece_is_named(value: str | None) -> None:
    extra = {} if value is None else {"AGENTCORE_ALLOW_DEMO": value}
    with pytest.raises(ServeConfigError) as info:
        _resolve(**extra)
    text = " ".join(info.value.problems)
    for option in DOUBLE_OPTIONS:
        assert option in text


def test_an_explicit_double_path_does_not_bypass_the_protection() -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve("--tools", "testing.serve_demo:tools")
    assert "AGENTCORE_ALLOW_DEMO=1" in " ".join(info.value.problems)


def test_a_path_that_cannot_be_imported_is_a_clear_problem() -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve("--tools", "no.existe:x", AGENTCORE_ALLOW_DEMO="1")
    assert "no.existe:x" in " ".join(info.value.problems)
    with pytest.raises(ServeConfigError) as info:
        _resolve("--tools", "sin-dos-puntos", AGENTCORE_ALLOW_DEMO="1")
    assert "modulo:atributo" in " ".join(info.value.problems)


def test_missing_dsn_and_missing_keys_are_reported_together() -> None:
    with pytest.raises(ServeConfigError) as info:
        resolve_ports(_args(), {}, FakeClock(), FakeIds())
    text = " ".join(info.value.problems)
    assert "--dsn" in text and "AGENTCORE_KEYS_FINGERPRINT" in text


def test_with_the_demo_switch_the_doubles_are_listed() -> None:
    ports = _resolve(AGENTCORE_ALLOW_DEMO="1")
    assert set(ports.doubles) == {"tools", "authz", "transcript", "calibration", "classifier",
                                  "field-classifier", "grant-active", "identity"}




# --- pase de arreglos de la revisión final ------------------------------------------------------------


class _Piece:
    """Una pieza 'real' cualquiera: lo único que importa es que no sea un doble de `testing`."""


def real_piece(ctx: object) -> _Piece:
    return _Piece()


def real_grant(ctx: object) -> object:
    return lambda ref, now: True


def _real_args(tmp_path: object, **over: str) -> list[str]:
    from pathlib import Path

    from testing.fakes.identity import TestIdentityIssuer
    from tests.m09.test_identity_keys import _file

    issuer = TestIdentityIssuer(FakeClock())
    keys = _file(Path(str(tmp_path)), issuer)
    paths = {"tools": "tests.composition.test_serve_ports:real_piece",
             "authz": "tests.composition.test_serve_ports:real_piece",
             "transcript": "tests.composition.test_serve_ports:real_piece",
             "calibration": "tests.composition.test_serve_ports:real_piece",
             "classifier": "tests.composition.test_serve_ports:real_piece",
             "field-classifier": "tests.composition.test_serve_ports:real_piece",
             "grant-active": "tests.composition.test_serve_ports:real_grant", **over}
    argv = ["--identity-keys", str(keys)]
    for name, path in paths.items():
        argv += [f"--{name}", path]
    return argv


def test_real_pieces_by_path_start_without_the_demo_switch(tmp_path: object) -> None:
    ports = _resolve(*_real_args(tmp_path))
    assert ports.doubles == ()
    assert type(ports.tools).__name__ == "_Piece"


def test_a_testing_double_path_is_rejected_without_the_demo_switch_even_if_everything_else_is_real(
        tmp_path: object) -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve(*_real_args(tmp_path, tools="testing.serve_demo:tools"))
    text = " ".join(info.value.problems)
    assert "tools" in text and "AGENTCORE_ALLOW_DEMO=1" in text


@pytest.mark.parametrize("path", ["..x:y", "os:getcwd", "builtins:print", "json:loads", "tests:__doc__"])
def test_any_failing_or_empty_factory_is_a_clean_problem_not_a_traceback(path: str) -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve("--tools", path, AGENTCORE_ALLOW_DEMO="1")
    assert path in " ".join(info.value.problems)


def test_a_missing_jev_key_is_a_config_error_that_is_not_swallowed_as_a_provider_error() -> None:
    from agent_core.decision import DecisionConfigError

    ports = _resolve(AGENTCORE_ALLOW_DEMO="1")
    transport = ports.providers["jev"]._transport  # type: ignore[attr-defined]
    with pytest.raises(DecisionConfigError, match="AGENTCORE_JEV_API_KEY"):
        transport.send({}, 1000)


# --- menores del revisor: validación temprana y agregada ----------------------------------------------

CALLS: list[str] = []


def counting_piece(ctx: object) -> _Piece:
    CALLS.append("called")
    return _Piece()


def test_invalid_llm_endpoints_is_a_config_problem_not_a_schema_error() -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve(AGENTCORE_ALLOW_DEMO="1", LLM_ENDPOINTS="{no es json")
    assert "LLM_ENDPOINTS" in " ".join(info.value.problems)


def test_static_config_problems_are_reported_together_before_any_factory_runs(tmp_path: object) -> None:
    from pathlib import Path

    bad_keys = Path(str(tmp_path)) / "keys.yaml"
    bad_keys.write_text('{"principal_keys": {}}', encoding="utf-8")
    CALLS.clear()
    with pytest.raises(ServeConfigError) as info:
        _resolve("--identity-keys", str(bad_keys), "--tools",
                 "tests.composition.test_serve_ports:counting_piece",
                 AGENTCORE_ALLOW_DEMO="1", LLM_ENDPOINTS="{no es json")
    text = " ".join(info.value.problems)
    assert "principal_keys" in text and "LLM_ENDPOINTS" in text
    assert CALLS == []  # las fábricas no corrieron con la configuración estática rota


def test_the_dsn_never_reaches_stderr(capsys: pytest.CaptureFixture[str],
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.cli import main

    monkeypatch.delenv("AGENTCORE_ALLOW_DEMO", raising=False)
    monkeypatch.setenv("LLM_ENDPOINTS", "{no es json")
    code = main(["serve", "--dsn", "postgresql://user:S3CRETPW@host/db"])
    assert code == 2
    assert "S3CRETPW" not in capsys.readouterr().err


def test_dsn_help_recommends_the_env_var_over_argv(capsys: pytest.CaptureFixture[str]) -> None:
    from agent_core.cli import main

    with pytest.raises(SystemExit):
        main(["serve", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "AGENTCORE_REGISTRY_DSN" in out and "lista de procesos" in out


def test_serve_wires_the_registry_directory() -> None:
    from agent_core.registry import RegistryDirectory

    ports = _resolve(AGENTCORE_ALLOW_DEMO="1")
    assert isinstance(ports.directory, RegistryDirectory)
    assert "directory" not in ports.doubles  # a real piece over the registry, not a demo double
