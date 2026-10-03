"""`TestIdentityIssuer`: el servicio de identidad de la demo (M9 §10, ADR 0010).

Firma con claves Ed25519 de PRUEBA derivadas de una semilla fija y pública en el repo (por eso el `kid`
empieza con `test-`): sirven para la demo y las pruebas, nunca para producción. Con la misma semilla y el
mismo `Clock`, emite los mismos tokens (Ed25519 es determinista). El OTP de step-up es simulado y va marcado
(`auth.simulated`)."""

import hashlib
from datetime import timedelta
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_core.adapters.jws_identity import (
    ALG,
    DELEGATION_TYP,
    PRINCIPAL_TYP,
    JwsIdentityVerifier,
    b64url_encode,
)
from agent_core.domain import OnBehalfOf, Principal, dumps
from agent_core.ports import Clock

b64 = b64url_encode

__all__ = ["DELEGATION_TYP", "PRINCIPAL_TYP", "TestIdentityIssuer", "TestStaffIssuer", "b64", "sign_jws"]


def sign_jws(header: dict[str, Any], payload: bytes, key: Ed25519PrivateKey) -> str:
    head = b64url_encode(dumps(header).encode("utf-8"))
    body = b64url_encode(payload)
    return f"{head}.{body}.{b64url_encode(key.sign(f'{head}.{body}'.encode('ascii')))}"


def _key(label: bytes) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(b"agent-core TEST ONLY key: " + label).digest()
    )


class TestIdentityIssuer:
    __test__ = False  # no es una clase de pruebas para pytest

    principal_kid = "test-principal-1"
    delegation_kid = "test-grant-1"

    def __init__(self, clock: Clock, *, ttl: timedelta = timedelta(hours=1)) -> None:
        self._clock = clock
        self._ttl = ttl
        self.principal_key = _key(b"principal")
        self.delegation_key = _key(b"delegation")
        self._revoked: set[str] = set()

    # --- firma ---------------------------------------------------------------------------------------

    def issue(self, who: Principal) -> str:
        header = {"alg": ALG, "kid": self.principal_kid, "typ": PRINCIPAL_TYP}
        return sign_jws(header, dumps(who).encode("utf-8"), self.principal_key)

    def issue_delegation(self, obo: OnBehalfOf) -> str:
        header = {"alg": ALG, "kid": self.delegation_kid, "typ": DELEGATION_TYP}
        return sign_jws(header, dumps(obo).encode("utf-8"), self.delegation_key)

    def verifier(self) -> JwsIdentityVerifier:
        return JwsIdentityVerifier(
            principal_keys={self.principal_kid: self.principal_key.public_key()},
            delegation_keys={self.delegation_kid: self.delegation_key.public_key()},
            grant_active=self.grant_active,
        )

    # --- asignaciones --------------------------------------------------------------------------------

    def revoke(self, grant_ref: str) -> None:
        self._revoked.add(grant_ref)

    def grant_active(self, grant_ref: str, now: Any) -> bool:
        return grant_ref not in self._revoked

    # --- los principales de la demo ------------------------------------------------------------------

    def _principal(self, **over: Any) -> Principal:
        now = self._clock.now()
        base: dict[str, Any] = {
            "type": "customer",
            "id": "cust-001",
            "attrs": {"country": "CO", "segment": "demo"},
            "auth": {"level": "session", "at": now},
            "exp": now + self._ttl,
        }
        return Principal.model_validate(base | over)

    def customer(self, customer_id: str = "cust-001") -> str:
        return self.issue(self._principal(id=customer_id))

    def advisor(self, advisor_id: str = "adv-7", subject_ref: str = "cust-001") -> tuple[str, str]:
        """`(principal, delegación)`: la delegación ata al asesor a ese cliente."""
        who = self._principal(type="advisor", id=advisor_id, attrs={})
        obo = OnBehalfOf.model_validate(
            {
                "subject": {"kind": "customer", "ref": subject_ref},
                "grant_ref": f"grant-{advisor_id}-{subject_ref}",
                "grantee": {"type": "advisor", "id": advisor_id},
                "scopes": ["read"],
                "exp": self._clock.now() + self._ttl,
            }
        )
        return self.issue(who), self.issue_delegation(obo)

    def anonymous(self, session: str = "anon-1") -> str:
        who = self._principal(
            id=None,
            attrs={"anon_session": session},
            auth={"level": "anonymous", "at": self._clock.now()},
        )
        return self.issue(who)

    def expired(self) -> str:
        now = self._clock.now()
        past = now - timedelta(minutes=1)
        return self.issue(self._principal(auth={"level": "session", "at": past - self._ttl}, exp=past))

    def stepped_up(self, customer_id: str = "cust-001") -> str:
        """Principal elevado tras un OTP SIMULADO (`auth.simulated`)."""
        auth = {"level": "step_up", "at": self._clock.now(), "simulated": True}
        return self.issue(self._principal(id=customer_id, auth=auth))


class TestStaffIssuer(TestIdentityIssuer):
    """El emisor del staff de la plataforma (supervisor, administrador y bot constructor), con una clave y un
    `kid` distintos de los del emisor de clientes y asesores (ADR 0006, tema #14). Las personas entran con
    `step_up` (OTP simulado); el bot no lleva `actor` ni más rol que `constructor`."""

    principal_kid = "test-staff-1"

    def __init__(self, clock: Clock, *, ttl: timedelta = timedelta(hours=1)) -> None:
        super().__init__(clock, ttl=ttl)
        self.principal_key = _key(b"staff")

    def _staff(self, pid: str, roles: list[str], actor: bool, level: str) -> str:
        now = self._clock.now()
        who = Principal.model_validate({
            "type": "builder", "id": pid, "roles": roles, "attrs": {"actor": "human"} if actor else {},
            "auth": {"level": level, "at": now, "simulated": True} if level == "step_up"
            else {"level": level, "at": now},
            "exp": now + self._ttl})
        return self.issue(who)

    def supervisor(self, pid: str = "ana", *, level: str = "step_up") -> str:
        return self._staff(pid, ["constructor", "aprobador"], True, level)

    def admin(self, pid: str = "root", *, level: str = "step_up") -> str:
        return self._staff(pid, ["constructor", "aprobador", "admin"], True, level)

    def constructor_bot(self, pid: str = "constructor-bot") -> str:
        return self._staff(pid, ["constructor"], False, "session")

    def exporter_bot(self, pid: str = "ingest-bot") -> str:
        """Servicio de ingesta: solo lee las exportaciones (N-08)."""
        return self._staff(pid, ["exporter"], False, "session")

