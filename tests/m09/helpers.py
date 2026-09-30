"""Dobles y constructores de M9. Todo sintético: tokens opacos de prueba, nunca una credencial real."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from agent_core.domain import (
    Awaiting,
    CredentialsInvalid,
    EngineEvent,
    Message,
    OnBehalfOf,
    Principal,
    RunInput,
    RunResult,
    TurnInput,
    TurnResult,
)
from testing.builders import NOW, principal

ANON_ATTR = "anon_session"


def anonymous(session: str = "anon-1", **over: Any) -> Principal:
    base: dict[str, Any] = {
        "type": "customer",
        "id": None,
        "attrs": {ANON_ATTR: session},
        "auth": {"level": "anonymous", "at": NOW},
    }
    return principal(**(base | over))


def delegation(*, grantee_id: str = "adv-7", subject_ref: str = "cust-001", **over: Any) -> OnBehalfOf:
    base: dict[str, Any] = {
        "subject": {"kind": "customer", "ref": subject_ref},
        "grant_ref": "grant-9",
        "grantee": {"type": "advisor", "id": grantee_id},
        "scopes": ["read"],
        "exp": NOW + timedelta(hours=1),
    }
    return OnBehalfOf.model_validate(base | over)


class StubVerifier:
    """`IdentityVerifier` de prueba: tokens opacos registrados de antemano; el resto es firma inválida."""

    def __init__(self) -> None:
        self._principals: dict[str, Principal] = {}
        self._delegations: dict[str, OnBehalfOf] = {}
        self.revoked: set[str] = set()
        self.verify_calls = 0

    def register(self, token: str, who: Principal | OnBehalfOf) -> str:
        if isinstance(who, Principal):
            self._principals[token] = who
        else:
            self._delegations[token] = who
        return token

    def verify(self, raw_credential: str) -> Principal:
        self.verify_calls += 1
        try:
            return self._principals[raw_credential]
        except KeyError:
            raise CredentialsInvalid("firma inválida") from None

    def verify_delegation(self, raw: str) -> OnBehalfOf:
        try:
            return self._delegations[raw]
        except KeyError:
            raise CredentialsInvalid("firma inválida") from None

    def grant_active(self, grant_ref: str, now: Any) -> bool:
        return grant_ref not in self.revoked


@dataclass
class RecordingDenials:
    """Doble de `AuditLog.append_standalone`."""

    calls: list[tuple[str, list[EngineEvent]]] = field(default_factory=list)
    fail: bool = False

    def append_standalone(self, run_id: str, events: list[EngineEvent]) -> Sequence[object]:
        if self.fail:
            raise RuntimeError("cadena no disponible")
        self.calls.append((run_id, events))
        return events


@dataclass
class RecordingSecurityLog:
    entries: list[dict[str, str | None]] = field(default_factory=list)

    def record(self, reason: str, principal_type: str | None, trace_id: str) -> None:
        self.entries.append({"reason": reason, "principal_type": principal_type, "trace_id": trace_id})


class FakeTurns:
    """Doble de M4 (`start_run` / `handle_turn`): registra las llamadas y devuelve resultados fijos."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Principal, OnBehalfOf | None, Any]] = []
        self.turn_result: TurnResult = turn_result()
        self.error: Exception | None = None

    def start_run(
        self, principal: Principal, on_behalf_of: OnBehalfOf | None, run_input: RunInput
    ) -> RunResult:
        self.calls.append(("start_run", principal, on_behalf_of, run_input))
        if self.error is not None:
            raise self.error
        return RunResult(
            run_id="run-0001",
            session_id="session-0001",
            release="rel-atencion",
            status="open",
            first_turn=self.turn_result,
            trace_id="engine-trace",
        )

    def handle_turn(
        self, principal: Principal, on_behalf_of: OnBehalfOf | None, turn: TurnInput
    ) -> TurnResult:
        self.calls.append(("handle_turn", principal, on_behalf_of, turn))
        if self.error is not None:
            raise self.error
        return self.turn_result


def turn_result(**over: Any) -> TurnResult:
    base: dict[str, Any] = {
        "run_id": "run-0001",
        "turn_id": "turn-0001",
        "messages": [Message(kind="generated", text="hola", locale="es")],
        "locale": "es",
        "awaiting": Awaiting.none,
        "status": "open",
        "trace_id": "engine-trace",
    }
    return TurnResult.model_validate(base | over)


@dataclass
class FakeHandoffs:
    calls: list[tuple[str, Any]] = field(default_factory=list)
    error: Exception | None = None

    def get(
        self, handoff_ref: str, reader: Principal, on_behalf_of: OnBehalfOf | None = None
    ) -> dict[str, Any]:
        self.calls.append(("get", (handoff_ref, reader, on_behalf_of)))
        if self.error is not None:
            raise self.error
        return {"handoff_ref": handoff_ref, "resolution": None}

    def record_resolution(
        self,
        handoff_ref: str,
        reader: Principal,
        resolution_code: str,
        handoff_quality: str,
        notes: str | None = None,
        *,
        on_behalf_of: OnBehalfOf | None = None,
    ) -> object:
        self.calls.append(
            ("resolve", (handoff_ref, reader, resolution_code, handoff_quality, notes, on_behalf_of))
        )
        if self.error is not None:
            raise self.error
        return object()


@dataclass
class Entry:
    turn_id: str
    role: str
    text: str
    reason: str | None = None
    unknown_tokens: list[str] = field(default_factory=list)


@dataclass
class FakeTranscripts:
    calls: list[tuple[str, Principal, OnBehalfOf | None]] = field(default_factory=list)
    error: Exception | None = None

    def read_rendered(self, run_id: str, reader: Principal, on_behalf_of: OnBehalfOf | None) -> list[Entry]:
        self.calls.append((run_id, reader, on_behalf_of))
        if self.error is not None:
            raise self.error
        return [Entry("turn-0001", "user", "hola"), Entry("turn-0001", "assistant", "¿en qué te ayudo?")]
