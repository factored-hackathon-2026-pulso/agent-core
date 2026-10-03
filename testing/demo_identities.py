"""Emite las credenciales de la demo: cliente, asesor con delegación, anónimo, vencido y elevado; y, con el
emisor del staff, supervisor, administrador y bot constructor.

    uv run python -m testing.demo_identities [--customer cust-001] [--advisor adv-7] [--anon-session anon-1]

Imprime un JSON `{nombre: JWS}`. Las firma `TestIdentityIssuer` con claves de PRUEBA públicas en el repo:
sirven para la demo, nunca para producción. Usa el reloj del sistema, así que `expired` ya está vencido y el
resto dura 1 hora."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agent_core.adapters.jws_identity import b64url_encode
from agent_core.adapters.system_clock import SystemClock
from agent_core.ports import Clock
from testing.fakes.identity import TestIdentityIssuer, TestStaffIssuer

NOTE = "Credenciales de PRUEBA firmadas con claves TEST públicas del repo; no son de producción."


def _public(key: Ed25519PrivateKey) -> str:
    return b64url_encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def main(argv: Sequence[str] | None = None, *, clock: Clock | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo_identities", description=__doc__)
    parser.add_argument("--customer", default="cust-001")
    parser.add_argument("--advisor", default="adv-7")
    parser.add_argument("--anon-session", default="anon-1")
    parser.add_argument("--public-keys", type=Path, default=None,
                        help="escribe las claves públicas del emisor para `agentcore serve --identity-keys`")
    parser.add_argument("--staff-keys", type=Path, default=None,
                        help="escribe las claves públicas del emisor del staff para `serve --staff-keys`")
    args = parser.parse_args(argv)
    clock = clock or SystemClock()
    issuer = TestIdentityIssuer(clock)
    staff = TestStaffIssuer(clock)
    advisor, delegation = issuer.advisor(args.advisor, args.customer)
    if args.public_keys is not None:
        # También la clave del staff: así un supervisor puede hablar con el agente constructor por /v1/runs.
        args.public_keys.write_text(json.dumps({
            "principal_keys": {issuer.principal_kid: _public(issuer.principal_key),
                               staff.principal_kid: _public(staff.principal_key)},
            "delegation_keys": {issuer.delegation_kid: _public(issuer.delegation_key)},
        }, indent=2), encoding="utf-8")
    if args.staff_keys is not None:
        staff_public = {"principal_keys": {staff.principal_kid: _public(staff.principal_key)}}
        args.staff_keys.write_text(json.dumps(staff_public, indent=2), encoding="utf-8")
    tokens = {
        "_note": NOTE,
        "customer": issuer.customer(args.customer),
        "advisor": advisor,
        "advisor_delegation": delegation,
        "anonymous": issuer.anonymous(args.anon_session),
        "expired": issuer.expired(),
        "stepped_up": issuer.stepped_up(args.customer),
        # Emisor del staff (clave distinta): solo lo acepta la API del registry.
        "supervisor": staff.supervisor(),
        "admin": staff.admin(),
        "constructor_bot": staff.constructor_bot(),
    }
    print(json.dumps(tokens, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
