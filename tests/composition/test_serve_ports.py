"""Puertos de `agentcore serve`: piezas reales por defecto; dobles solo con AGENTCORE_ALLOW_DEMO=1."""

import argparse
import base64
from pathlib import Path

import pytest

from agent_core.adapters.llm import HttpLLMGateway
from agent_core.composition.serve_ports import ServeConfigError, ServePorts, add_serve_parser, resolve_ports
from agent_core.domain import EntityRef, GatewayError, GatewayErrorKind
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds

pytest_plugins = ["tests.support.otel"]

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




def test_a_hand_built_namespace_without_the_optional_attributes_still_resolves(tmp_path: Path) -> None:
    """Contrato mínimo: `lang_thresholds` y `agents` son opcionales; quien arma el Namespace a mano (sin pasar
    por `add_serve_parser`) no tiene por qué conocerlos."""
    args = _args()
    del args.lang_thresholds, args.agents
    ports = resolve_ports(args, _env(AGENTCORE_ALLOW_DEMO="1"), FakeClock(), FakeIds())
    assert ports.lang_thresholds == {} and ports.agents == ()
    path = tmp_path / "lang.json"
    path.write_text('{"lang-cal": {"switch_threshold": 0.9, "unsupported_threshold": 0.95, '
                    '"min_distance": 0.2}}', encoding="utf-8")
    via_env = resolve_ports(args, _env(AGENTCORE_ALLOW_DEMO="1", AGENTCORE_LANG_THRESHOLDS=str(path),
                                       AGENTCORE_SERVE_AGENTS="a, b"), FakeClock(), FakeIds())
    assert set(via_env.lang_thresholds) == {"lang-cal"} and via_env.agents == ("a", "b")


def test_lang_thresholds_come_from_a_json_file_and_default_to_none(tmp_path: Path) -> None:
    assert _resolve(AGENTCORE_ALLOW_DEMO="1").lang_thresholds == {}
    path = tmp_path / "lang.json"
    path.write_text('{"lang-cal": {"switch_threshold": 0.9, "unsupported_threshold": 0.95, '
                    '"min_distance": 0.2}}', encoding="utf-8")
    ports = _resolve("--lang-thresholds", str(path), AGENTCORE_ALLOW_DEMO="1")
    assert ports.lang_thresholds["lang-cal"].switch_threshold == 0.9
    assert ports.lang_thresholds["lang-cal"].switch_active
    via_env = _resolve(AGENTCORE_ALLOW_DEMO="1", AGENTCORE_LANG_THRESHOLDS=str(path))
    assert set(via_env.lang_thresholds) == {"lang-cal"}


@pytest.mark.parametrize("content", ["no es json", "[1, 2]", '{"x": {"switch_threshold": 7}}'])
def test_a_broken_lang_thresholds_file_is_a_startup_problem_not_a_silent_off(tmp_path: Path,
                                                                             content: str) -> None:
    path = tmp_path / "lang.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ServeConfigError) as info:
        _resolve("--lang-thresholds", str(path), AGENTCORE_ALLOW_DEMO="1")
    assert "--lang-thresholds" in " ".join(info.value.problems)
    assert content not in " ".join(info.value.problems)  # el contenido del archivo no se imprime


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


def test_an_invalid_llm_gateway_url_is_a_config_problem_not_a_schema_error() -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve(AGENTCORE_ALLOW_DEMO="1", AGENTCORE_LLM_GATEWAY_URL="no es una url",
                 AGENTCORE_LLM_GATEWAY_TOKEN="t")
    assert "AGENTCORE_LLM_GATEWAY_URL" in " ".join(info.value.problems)


@pytest.mark.parametrize("env", [{"AGENTCORE_LLM_GATEWAY_URL": "https://gw.test"},
                                 {"AGENTCORE_LLM_GATEWAY_TOKEN": "t"}])
def test_a_half_configured_llm_gateway_is_a_config_problem(env: dict[str, str]) -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve(AGENTCORE_ALLOW_DEMO="1", **env)
    text = " ".join(info.value.problems)
    assert "AGENTCORE_LLM_GATEWAY_URL" in text and "AGENTCORE_LLM_GATEWAY_TOKEN" in text


def test_static_config_problems_are_reported_together_before_any_factory_runs(tmp_path: object) -> None:
    from pathlib import Path

    bad_keys = Path(str(tmp_path)) / "keys.yaml"
    bad_keys.write_text('{"principal_keys": {}}', encoding="utf-8")
    CALLS.clear()
    with pytest.raises(ServeConfigError) as info:
        _resolve("--identity-keys", str(bad_keys), "--tools",
                 "tests.composition.test_serve_ports:counting_piece",
                 AGENTCORE_ALLOW_DEMO="1", AGENTCORE_LLM_GATEWAY_URL="no es una url",
                 AGENTCORE_LLM_GATEWAY_TOKEN="t")
    text = " ".join(info.value.problems)
    assert "principal_keys" in text and "AGENTCORE_LLM_GATEWAY_URL" in text
    assert CALLS == []  # las fábricas no corrieron con la configuración estática rota


def test_the_dsn_never_reaches_stderr(capsys: pytest.CaptureFixture[str],
                                      monkeypatch: pytest.MonkeyPatch, root_logging: None,
        no_otel_env: None) -> None:
    from agent_core.cli import main

    monkeypatch.delenv("AGENTCORE_ALLOW_DEMO", raising=False)
    monkeypatch.setenv("AGENTCORE_LLM_GATEWAY_URL", "no es una url")
    monkeypatch.setenv("AGENTCORE_LLM_GATEWAY_TOKEN", "t")
    code = main(["serve", "--dsn", "postgresql://user:S3CRETPW@host/db"])
    assert code == 2
    assert "S3CRETPW" not in capsys.readouterr().err


def test_dsn_help_recommends_the_env_var_over_argv(capsys: pytest.CaptureFixture[str]) -> None:
    from agent_core.cli import main

    with pytest.raises(SystemExit):
        main(["serve", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "AGENTCORE_REGISTRY_DSN" in out and "lista de procesos" in out


# --- observabilidad (refactor del plano operativo, tarea 2) --------------------------------------------


def test_a_configured_llm_gateway_is_the_http_client() -> None:
    ports = _resolve(AGENTCORE_ALLOW_DEMO="1", AGENTCORE_LLM_GATEWAY_URL="https://gw.test/",
                     AGENTCORE_LLM_GATEWAY_TOKEN="t")
    assert isinstance(ports.gateway, HttpLLMGateway) and ports.llm_gateway_url == "https://gw.test/"


def test_without_a_gateway_every_generation_fails_as_unavailable_and_startup_is_not_blocked() -> None:
    ports = _resolve(AGENTCORE_ALLOW_DEMO="1")
    assert ports.llm_gateway_url is None
    with pytest.raises(GatewayError) as caught:
        ports.gateway.generate(EntityRef.parse("p@1.0.0"), {}, "es")
    assert caught.value.kind is GatewayErrorKind.unavailable


def boom_factory(ctx: object) -> object:
    raise RuntimeError("SECRETO-de-fabrica")


def test_a_failing_piece_factory_reports_only_the_exception_type() -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve("--tools", "tests.composition.test_serve_ports:boom_factory", AGENTCORE_ALLOW_DEMO="1")
    text = " ".join(info.value.problems)
    assert "RuntimeError" in text and "SECRETO" not in text


def test_serve_wires_the_registry_directory() -> None:
    from agent_core.registry import RegistryDirectory

    ports = _resolve(AGENTCORE_ALLOW_DEMO="1")
    assert isinstance(ports.directory, RegistryDirectory)
    assert "directory" not in ports.doubles  # a real piece over the registry, not a demo double


def test_serve_registers_a_postgres_readiness_check_that_fails_closed_when_unreachable() -> None:
    ports = _resolve("--tools", "testing.serve_demo:tools", "--authz", "testing.serve_demo:authz",
                     "--transcript", "testing.serve_demo:transcript",
                     "--calibration", "testing.serve_demo:calibration",
                     "--classifier", "testing.serve_demo:classifier_provider",
                     "--field-classifier", "testing.serve_demo:field_classifier",
                     "--grant-active", "testing.serve_demo:grant_active",
                     AGENTCORE_ALLOW_DEMO="1",
                     AGENTCORE_REGISTRY_DSN="postgresql://u:secret@127.0.0.1:1/none")

    (name, check), = ports.readiness

    assert name == "postgres"
    assert check() is False  # nada escucha en el puerto 1: falla cerrado, sin lanzar


@pytest.mark.parametrize("value", ["-1", "diez", "1.5"])
def test_an_invalid_pool_size_is_a_named_problem(value: str) -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve(AGENTCORE_ALLOW_DEMO="1", AGENTCORE_DB_POOL_MAX=value)
    assert "AGENTCORE_DB_POOL_MAX" in " ".join(info.value.problems)


def test_a_valid_pool_size_composes_without_connecting() -> None:
    ports = _resolve(AGENTCORE_ALLOW_DEMO="1", AGENTCORE_DB_POOL_MAX="4")  # el pool se abre al primer uso

    assert ports.uow_factory is not None
