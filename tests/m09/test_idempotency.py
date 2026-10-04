"""M9 F6: `Idempotency-Key` de `POST /v1/runs` (T-M9-11, spec §3.3), sobre el `TurnEngine` real de M4."""

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_core.api.app import ApiDeps, create_app
from testing.builders import NOW, principal
from testing.fakes.authz import TableAuthz
from testing.fakes.storage import InMemoryCostCounters
from tests.m04.harness import World as EngineWorld
from tests.m09.helpers import (
    FakeHandoffs,
    FakeTranscripts,
    RecordingDenials,
    RecordingSecurityLog,
    StubVerifier,
    anonymous,
)
from tests.m09.test_api import World as FakeWorld
from tests.m09.test_api import problem


class Real:
    """La API delante del `TurnEngine` real (harness de M4): nada de M4 está doblado."""

    def __init__(self) -> None:
        self.engine = EngineWorld()
        self.verifier = StubVerifier()
        self.verifier.register("tok-c", self.engine.principal)
        self.verifier.register("tok-2", principal(id="cust-002"))
        deps = ApiDeps(
            verifier=self.verifier,
            authz=TableAuthz(),
            registry=self.engine.registry,
            uow_factory=self.engine.uow_factory,
            counters=InMemoryCostCounters(self.engine.store),
            clock=self.engine.clock,
            ids=self.engine.ids,
            turns=self.engine.engine,
            handoffs=FakeHandoffs(),
            transcripts=FakeTranscripts(),
            denials=RecordingDenials(),
            security=RecordingSecurityLog(),
        )
        self.client = TestClient(create_app(deps), raise_server_exceptions=False)

    def create(self, token: str = "tok-c", key: str = "key-1", **body: Any):  # type: ignore[no-untyped-def]
        return self.client.post(
            "/v1/runs",
            json={"agent": "atencion", **body},
            headers={"Authorization": token, "Idempotency-Key": key},
        )


def test_repeated_idempotency_key_returns_the_same_run() -> None:  # T-M9-11
    r = Real()
    first = r.create()
    again = r.create()
    assert first.status_code == 201 and again.status_code == 201
    assert again.json() == first.json()
    assert len(r.engine.store.runs) == 1


def test_same_key_with_another_body_is_409_idempotency_conflict() -> None:
    r = Real()
    assert r.create(lang="es").status_code == 201
    problem(r.create(lang="pt"), 409, "idempotency_conflict")
    assert len(r.engine.store.runs) == 1


def test_a_key_still_in_flight_is_409_idempotency_in_progress_not_conflict() -> None:
    """El cliente distingue por `code`, no por el texto: reintentar luego sirve, cambiar el body no."""
    r = Real()
    with r.engine.store.uow() as uow:  # otra petición con esta clave sigue trabajando
        assert uow.reserve_run_idempotency(r.engine.principal.key, "key-1", "otro", r.engine.clock.now(),
                                           timedelta(seconds=60)) is None
    problem(r.create(lang="es"), 409, "idempotency_in_progress")
    assert r.engine.store.runs == {}


def test_the_key_is_scoped_per_principal() -> None:
    r = Real()
    mine = r.create("tok-c")
    theirs = r.create("tok-2")
    assert mine.status_code == 201 and theirs.status_code == 201
    assert mine.json()["run_id"] != theirs.json()["run_id"]


def test_a_renewed_credential_replays_the_same_run() -> None:
    r = Real()
    first = r.create()
    r.verifier.register(
        "tok-new", principal(auth={"level": "step_up", "at": NOW}, exp=NOW + timedelta(hours=3))
    )
    again = r.create("tok-new")
    assert again.json() == first.json()


def test_the_replay_does_not_run_the_engine_again() -> None:
    r = Real()
    r.create()
    commits = r.engine.uow_factory.commits  # type: ignore[attr-defined]
    r.create()
    assert r.engine.uow_factory.commits == commits  # type: ignore[attr-defined]


@pytest.mark.parametrize("key", ["", "   ", "k" * 256])
def test_invalid_keys_are_422(key: str) -> None:
    w = FakeWorld()
    resp = w.client.post(
        "/v1/runs", json={"agent": "atencion"}, headers={"Authorization": "tok-c", "Idempotency-Key": key}
    )
    problem(resp, 422, "invalid_request")
    assert w.turns.calls == []


def test_anonymous_keys_are_namespaced_by_the_signed_session() -> None:
    """Todos los anónimos comparten `PrincipalKey(customer, None)`: sin el prefijo, dos anónimos con la misma
    clave se verían el run del otro."""
    w = FakeWorld()
    w.verifier.register("tok-1", anonymous("anon-1"))
    w.verifier.register("tok-2", anonymous("anon-2"))
    for token in ("tok-1", "tok-2"):
        assert w.create_run(token, body={"agent": "faq"}).status_code == 201
    keys = [call[3].idempotency_key for call in w.turns.calls]
    assert keys == ["anon-1:key-1", "anon-2:key-1"]


def test_identified_keys_are_passed_as_they_come() -> None:
    w = FakeWorld()
    w.create_run()
    assert w.turns.calls[0][3].idempotency_key == "key-1"
