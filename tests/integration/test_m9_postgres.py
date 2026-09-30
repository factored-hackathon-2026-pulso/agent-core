"""M9 sobre Postgres real: idempotencia, IDOR con `access_denied` encadenado y límites con `CostCounters`."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_core.adapters.postgres_uow import PostgresStore
from agent_core.api.app import ApiDeps, create_app
from agent_core.api.limits import RateLimitConfig
from agent_core.audit import AuditLog
from testing.builders import principal
from testing.fakes.authz import TableAuthz
from tests.integration.pg_world import PgWorld
from tests.m04.harness import RUN_ID, SESSION_ID
from tests.m04.helpers import cmd
from tests.m09.helpers import FakeHandoffs, FakeTranscripts, RecordingSecurityLog, StubVerifier
from tests.support.pg import postgres_store

pytestmark = pytest.mark.integration


class PgApi:
    def __init__(self, pg: PostgresStore, limits: RateLimitConfig | None = None) -> None:
        self.pg = pg
        self.w = PgWorld(pg)
        self.verifier = StubVerifier()
        self.verifier.register("tok-c", principal())
        self.verifier.register("tok-2", principal(id="cust-002"))
        deps = ApiDeps(
            verifier=self.verifier,
            authz=TableAuthz(),
            registry=self.w.registry,
            uow_factory=pg.uow,
            counters=pg.costs(),
            clock=self.w.clock,
            ids=self.w.ids,
            turns=self.w.engine,
            handoffs=FakeHandoffs(),
            transcripts=FakeTranscripts(),
            denials=AuditLog(pg.audit(), pg.uow),
            security=RecordingSecurityLog(),
            limits=limits or RateLimitConfig(),
        )
        self.client = TestClient(create_app(deps), raise_server_exceptions=False)

    def call(self, method: str, path: str, token: str = "tok-c", body: Any = None, **headers: str):  # type: ignore[no-untyped-def]
        return self.client.request(method, path, json=body, headers={"Authorization": token, **headers})

    def denials(self) -> list[str]:
        return [
            str(e.payload.reason)  # type: ignore[attr-defined]
            for e in self.pg.audit().read(RUN_ID)
            if e.type == "access_denied"
        ]


def test_create_run_is_idempotent_on_postgres() -> None:  # T-M9-11
    with postgres_store("m9_idem") as pg:
        api = PgApi(pg)
        headers = {"Idempotency-Key": "key-1"}
        first = api.call("POST", "/v1/runs", body={"agent": "atencion"}, **headers)
        again = api.call("POST", "/v1/runs", body={"agent": "atencion"}, **headers)
        assert first.status_code == again.status_code == 201
        assert again.json() == first.json()
        conflict = api.call("POST", "/v1/runs", body={"agent": "atencion", "lang": "pt"}, **headers)
        assert conflict.status_code == 409 and conflict.json()["code"] == "idempotency_conflict"


def test_idor_is_denied_and_recorded_in_a_valid_chain() -> None:  # T-M9-01 / T-M9-09
    with postgres_store("m9_idor") as pg:
        api = PgApi(pg)
        api.w.open_run()
        read = api.call("GET", f"/v1/runs/{RUN_ID}", "tok-2")
        assert read.status_code == 403 and read.json()["code"] == "subject_forbidden"
        turn = api.call(
            "POST",
            f"/v1/sessions/{SESSION_ID}/turns",
            "tok-2",
            {"text": "hola", "channel": "web", "client_turn_id": "ct-1"},
        )
        assert turn.status_code == 403 and turn.json()["code"] == "principal_mismatch"
        assert api.denials() == ["subject_forbidden", "principal_mismatch"]
        assert AuditLog(pg.audit(), pg.uow).verify_chain(RUN_ID).ok
        assert api.call("GET", f"/v1/runs/{RUN_ID}").status_code == 200  # el dueño sigue entrando


def test_rate_limit_counts_what_the_engine_records() -> None:  # T-M9-12
    with postgres_store("m9_rate") as pg:
        api = PgApi(pg, RateLimitConfig(max_hits=1))
        api.w.open_run(active=True)
        api.w.understand.push(cmd("out_of_scope"))
        body = {"text": "hola", "channel": "web", "client_turn_id": "ct-1"}
        path = f"/v1/sessions/{SESSION_ID}/turns"
        assert api.call("POST", path, body=body).status_code == 200
        limited = api.call("POST", path, body={**body, "client_turn_id": "ct-2"})
        assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
