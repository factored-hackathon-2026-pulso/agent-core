"""`serve --registry-api`: servicio con evaluación real, verificador de staff y base de evaluación propia."""

import argparse
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from agent_core.cli import main
from agent_core.composition import EngineScenarioHarness
from agent_core.composition.serve import build_api_deps
from agent_core.composition.serve_ports import ServeConfigError, ServePorts
from agent_core.composition.serve_registry import RegistryApiPorts, build_registry_service_for_serve
from agent_core.registry import ScenarioEvaluator
from agent_core.registry.memory import InMemoryRegistryStore
from testing.engine_world import EngineWorld
from testing.fakes.clock import FakeClock
from testing.fakes.identity import TestIdentityIssuer, TestStaffIssuer
from testing.fakes.ids import FakeIds
from testing.fakes.storage import InMemoryAuditSink
from tests.composition.test_serve_app import make_ports
from tests.composition.test_serve_ports import _args, _env, _real_args
from tests.registry.helpers import AGENT

pytest_plugins = ["tests.support.otel"]

BODY = {"agent_id": AGENT, "origin": "manual", "title": "t"}


def _api_ports(world: EngineWorld, staff: TestStaffIssuer) -> RegistryApiPorts:
    return RegistryApiPorts(store=InMemoryRegistryStore(), staff_verifier=staff.verifier(),
                            eval_uow_factory=world.store.uow, eval_audit=InMemoryAuditSink(world.store))


def _ports(world: EngineWorld, issuer: TestIdentityIssuer, staff: TestStaffIssuer | None) -> ServePorts:
    base = make_ports(world, issuer)
    api = None if staff is None else _api_ports(world, staff)
    return replace(base, registry_api=api)


def _client(staff: TestStaffIssuer | None) -> tuple[TestClient, TestIdentityIssuer]:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    ports = _ports(world, issuer, staff)
    service = None if staff is None else build_registry_service_for_serve(ports)
    app = create_app(build_api_deps(ports, registry_service=service))
    return TestClient(app, raise_server_exceptions=False), issuer


def test_with_the_registry_api_staff_operate_and_customers_do_not() -> None:
    staff = TestStaffIssuer(FakeClock())
    client, issuer = _client(staff)
    ok = client.post("/v1/registry/proposals", json=BODY,
                     headers={"Authorization": f"Bearer {staff.supervisor()}"})
    assert ok.status_code == 201, ok.text
    no = client.post("/v1/registry/proposals", json=BODY,
                     headers={"Authorization": f"Bearer {issuer.customer()}"})
    assert no.status_code == 401


def test_without_the_flag_there_are_no_registry_routes() -> None:
    client, issuer = _client(None)
    headers = {"Authorization": f"Bearer {issuer.customer()}"}
    r = client.post("/v1/registry/proposals", json=BODY, headers=headers)
    assert r.status_code == 404


def test_the_service_evaluates_with_the_real_engine_harness_over_the_serve_pieces() -> None:
    world = EngineWorld()
    ports = _ports(world, TestIdentityIssuer(world.clock), TestStaffIssuer(world.clock))
    service = build_registry_service_for_serve(ports)
    evaluator = service._evaluator  # type: ignore[attr-defined]
    assert isinstance(evaluator, ScenarioEvaluator)
    harness = evaluator._harness  # type: ignore[attr-defined]
    assert isinstance(harness, EngineScenarioHarness)
    assert harness._gateway is ports.gateway  # type: ignore[attr-defined]
    assert harness._providers("cualquiera") is ports.providers  # type: ignore[attr-defined]
    assert harness._storage().transcript is ports.transcript  # type: ignore[attr-defined]
    assert harness._storage().audit is ports.registry_api.eval_audit  # type: ignore[union-attr,attr-defined]


# --- resolve_ports -----------------------------------------------------------------------------------------


def _resolve(*argv: str, **env: str) -> ServePorts:
    from agent_core.composition.serve_ports import resolve_ports

    return resolve_ports(_args(*argv), _env(**env), FakeClock(), FakeIds())


def test_the_flag_adds_its_problems_to_the_single_list(tmp_path: Path) -> None:
    bad = tmp_path / "staff.yaml"
    bad.write_text('{"principal_keys": {}}', encoding="utf-8")
    with pytest.raises(ServeConfigError) as info:
        _resolve(*_real_args(tmp_path), "--registry-api", "--staff-keys", str(bad))
    text = " ".join(info.value.problems)
    assert "--eval-dsn" in text and "principal_keys" in text


def test_outside_demo_the_flag_needs_staff_keys(tmp_path: Path) -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve(*_real_args(tmp_path), "--registry-api", "--eval-dsn", "postgresql://ignored/ev")
    assert "--staff-keys" in " ".join(info.value.problems)


def test_in_demo_the_staff_verifier_is_a_listed_double_and_the_eval_dsn_can_come_from_env() -> None:
    ports = _resolve("--registry-api", AGENTCORE_ALLOW_DEMO="1", AGENTCORE_EVAL_DSN="postgresql://ignored/ev")
    assert ports.registry_api is not None and "staff-identity" in ports.doubles


def test_a_staff_keys_file_without_delegation_keys_is_enough(tmp_path: Path) -> None:
    import json

    from tests.m09.test_identity_keys import _pub

    staff = TestStaffIssuer(FakeClock())
    keys = tmp_path / "staff.yaml"
    keys.write_text(json.dumps({"principal_keys": {staff.principal_kid: _pub(staff.principal_key)}}),
                    encoding="utf-8")
    ports = _resolve(*_real_args(tmp_path), "--registry-api", "--staff-keys", str(keys),
                     "--eval-dsn", "postgresql://ignored/ev")
    assert ports.registry_api is not None and "staff-identity" not in ports.doubles
    assert ports.registry_api.staff_verifier.verify(staff.supervisor()).id == "ana"


def test_the_eval_dsn_never_reaches_stderr(capsys: pytest.CaptureFixture[str],
                                           monkeypatch: pytest.MonkeyPatch, root_logging: None,
        no_otel_env: None) -> None:
    monkeypatch.delenv("AGENTCORE_ALLOW_DEMO", raising=False)
    code = main(["serve", "--dsn", "postgresql://u:MAINPW@h/d", "--registry-api",
                 "--eval-dsn", "postgresql://u:EVALPW@h/e"])
    err = capsys.readouterr().err
    assert code == 2 and "--staff-keys" in err
    assert "EVALPW" not in err and "MAINPW" not in err


def test_help_lists_the_new_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["serve", "--help"])
    out = capsys.readouterr().out
    assert "--registry-api" in out and "--eval-dsn" in out and "--staff-keys" in out
    assert argparse


def test_rate_limits_come_from_the_environment_and_bad_values_are_rejected() -> None:
    from datetime import timedelta
    from decimal import Decimal

    from agent_core.composition.serve import rate_limits_from_env

    config = rate_limits_from_env({"AGENTCORE_RATE_MAX_HITS": "120", "AGENTCORE_RATE_WINDOW_SECONDS": "30",
                                   "AGENTCORE_DAILY_BUDGET_USD": "50.5"})
    assert (config.max_hits, config.window, config.daily_budget_usd) == (120, timedelta(seconds=30),
                                                                         Decimal("50.5"))
    assert rate_limits_from_env({}).max_hits == 30  # sin variables, los valores de demo
    for bad in ({"AGENTCORE_RATE_MAX_HITS": "mucho"}, {"AGENTCORE_RATE_MAX_HITS": "0"},
                {"AGENTCORE_DAILY_BUDGET_USD": "-1"}):
        with pytest.raises(ValueError):
            rate_limits_from_env(bad)
