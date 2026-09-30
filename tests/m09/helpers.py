"""Dobles y constructores de M9. Todo sintético: tokens opacos de prueba, nunca una credencial real."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from agent_core.domain import CredentialsInvalid, EngineEvent, OnBehalfOf, Principal
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
