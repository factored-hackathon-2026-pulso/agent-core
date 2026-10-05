"""Pausar un agente: sale del directorio de `recepcion` sin tocar `prod`, y se reanuda."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_core.domain import CredentialsInvalid, Principal
from agent_core.registry import RegistryErrorCode, RegistryService
from agent_core.registry.errors import RegistryError
from agent_core.registry.http import registry_extension
from testing.builders import principal
from tests.registry.helpers import AGENT, bot, human
from tests.registry.service_world import World


def test_pause_keeps_prod_and_resume_restores_it() -> None:
    w = World()
    prod = w.store.transaction().__enter__().get_alias(AGENT, "prod")  # type: ignore[attr-defined]
    assert prod is not None

    paused = w.service.pause_agent(human(), AGENT, "revisión")

    assert (paused.paused, paused.release_id) == (True, prod)
    assert w.service.get_pause(AGENT).paused is True
    assert w.service.get_alias(AGENT, "prod").release_id == prod  # type: ignore[union-attr]
    resumed = w.service.resume_agent(human(), AGENT)
    assert (resumed.paused, resumed.release_id) == (False, prod)
    assert w.service.get_pause(AGENT).paused is False
    assert [e.type for e in w.store.transaction().__enter__().events()][-2:] == ["paused", "resumed"]  # type: ignore[attr-defined]


def test_pausing_twice_or_resuming_without_pause_is_a_conflict() -> None:
    w = World()
    with pytest.raises(RegistryError) as not_paused:
        w.service.resume_agent(human(), AGENT)
    assert not_paused.value.code is RegistryErrorCode.illegal_transition
    w.service.pause_agent(human(), AGENT)
    with pytest.raises(RegistryError) as twice:
        w.service.pause_agent(human(), AGENT)
    assert twice.value.code is RegistryErrorCode.illegal_transition


def test_an_agent_without_prod_cannot_be_paused() -> None:
    w = World()
    with pytest.raises(RegistryError) as err:
        w.service.pause_agent(human(), "no-existe")
    assert err.value.code is RegistryErrorCode.not_found


def test_pausing_needs_an_approver_human() -> None:
    w = World()
    with pytest.raises(RegistryError) as err:
        w.service.pause_agent(principal(type="builder", id="x", roles=["constructor"], attrs={}), AGENT)
    assert err.value.code is RegistryErrorCode.forbidden_role


def test_paused_agents_leave_the_directory_and_come_back() -> None:
    from pathlib import Path

    from agent_core.registry import PostgresRegistry, RegistryDirectory
    from agent_core.registry.memory import InMemoryRegistryStore
    from testing.fakes.clock import FakeClock
    from testing.fakes.ids import FakeIds
    from tests.registry.helpers import admin
    from tests.registry.service_world import FakeEvaluator

    store = InMemoryRegistryStore()
    service = RegistryService(store, FakeEvaluator(), FakeClock(), FakeIds())
    service.import_seed(admin(), Path("tests/fixtures/registry-transfer-demo"))
    runtime = PostgresRegistry(store, FakeClock())  # type: ignore[arg-type]
    directory = RegistryDirectory(store, runtime, runtime.release)

    def listed() -> list[str]:
        return [a.id for _, a in directory.members("atencion-cliente")]

    assert listed() == ["consultas", "disputas"]
    service.pause_agent(human(), "disputas")
    assert listed() == ["consultas"]
    service.resume_agent(human(), "disputas")
    assert listed() == ["consultas", "disputas"]


def test_pause_over_http() -> None:
    w = World()
    app = FastAPI()
    people: dict[str, Principal] = {"ana": human(), "bot": bot("constructor")}

    def authenticate(request: Request, authorization: str | None) -> Principal:
        if authorization not in people:
            raise CredentialsInvalid()
        return people[authorization]

    registry_extension(w.service)(app, authenticate)
    c = TestClient(app, raise_server_exceptions=False)
    ana = {"authorization": "ana"}

    assert c.get(f"/v1/registry/agents/{AGENT}/pause", headers=ana).json()["paused"] is False
    paused = c.post(f"/v1/registry/agents/{AGENT}/pause", json={"reason": "revisión"}, headers=ana)
    assert paused.status_code == 200 and paused.json()["paused"] is True
    assert c.post(f"/v1/registry/agents/{AGENT}/pause", json={"reason": "x"}, headers=ana).status_code == 409
    assert c.post(f"/v1/registry/agents/{AGENT}/pause", json={"reason": "x"},
                  headers={"authorization": "bot"}).status_code == 403
    resumed = c.post(f"/v1/registry/agents/{AGENT}/resume", json={"reason": "ok"}, headers=ana)
    assert resumed.status_code == 200 and resumed.json()["paused"] is False
