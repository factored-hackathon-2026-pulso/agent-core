"""Emite las credenciales de la demo: cliente, asesor con delegación, anónimo, vencido y elevado.

    uv run python -m testing.demo_identities [--customer cust-001] [--advisor adv-7] [--anon-session anon-1]

Imprime un JSON `{nombre: JWS}`. Las firma `TestIdentityIssuer` con claves de PRUEBA públicas en el repo:
sirven para la demo, nunca para producción. Usa el reloj del sistema, así que `expired` ya está vencido y el
resto dura 1 hora."""

import argparse
import json
from collections.abc import Sequence

from agent_core.adapters.system_clock import SystemClock
from agent_core.ports import Clock
from testing.fakes.identity import TestIdentityIssuer

NOTE = "Credenciales de PRUEBA firmadas con claves TEST públicas del repo; no son de producción."


def main(argv: Sequence[str] | None = None, *, clock: Clock | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo_identities", description=__doc__)
    parser.add_argument("--customer", default="cust-001")
    parser.add_argument("--advisor", default="adv-7")
    parser.add_argument("--anon-session", default="anon-1")
    args = parser.parse_args(argv)
    issuer = TestIdentityIssuer(clock or SystemClock())
    advisor, delegation = issuer.advisor(args.advisor, args.customer)
    tokens = {
        "_note": NOTE,
        "customer": issuer.customer(args.customer),
        "advisor": advisor,
        "advisor_delegation": delegation,
        "anonymous": issuer.anonymous(args.anon_session),
        "expired": issuer.expired(),
        "stepped_up": issuer.stepped_up(args.customer),
    }
    print(json.dumps(tokens, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
