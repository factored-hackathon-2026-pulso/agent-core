"""Check end-to-end del stack local: ¿sigue funcionando el sistema después de un cambio?

    uv run python scripts/e2e/check.py --base-url http://127.0.0.1:8010 [--conversations]

Habla con un `agentcore serve` ya levantado (lo arranca `check.ps1`), solo por HTTP y con datos
sintéticos (`cust-001`). Las credenciales las firma `TestIdentityIssuer` con las claves TEST del repo, así
que el servidor tiene que correr con `--identity-keys` de `testing.demo_identities` (como el resto del e2e).

Niveles:
- **plataforma** (siempre, sin keys ni costo): salud, versión del contrato, credenciales, topes de tamaño,
  idempotencia, IDOR, delegación y apertura de runs de los tres agentes.
- **conversaciones** (`--conversations`): escenarios del runbook con JEV y el LLM reales. Necesitan
  `AGENTCORE_JEV_API_KEY` en el entorno de `serve`; sin ella se marcan como omitidos.

Nunca imprime credenciales ni el texto de las respuestas: solo estados, códigos y `run_id` para
`report.ps1 -Run <id>`. Sale con 0 si nada falló."""

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from agent_core.adapters.system_clock import SystemClock
from agent_core.adapters.system_ids import SystemIds
from agent_core.domain import SCHEMA_VERSION
from agent_core.ports import IdKind
from testing.chat import ChatSession
from testing.fakes.identity import TestIdentityIssuer

CUSTOMER, OTHER_CUSTOMER, ADVISOR = "cust-001", "cust-002", "adv-7"
Json = dict[str, Any]


@dataclass
class Result:
    name: str
    status: str  # ok | fail | skip | info
    detail: str = ""


@dataclass
class Check:
    base_url: str
    gateway_url: str | None
    results: list[Result] = field(default_factory=list)
    issuer: TestIdentityIssuer = field(default_factory=lambda: TestIdentityIssuer(SystemClock()))
    ids: SystemIds = field(default_factory=SystemIds)

    # --- transporte ----------------------------------------------------------------------------------

    def call(self, method: str, path: str, token: str | None = None, body: Any = None,
             headers: Mapping[str, str] | None = None, raw: bytes | None = None,
             base: str | None = None) -> tuple[int, Json]:
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        if token is not None:
            hdrs["Authorization"] = f"Bearer {token}"
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        request = urllib.request.Request((base or self.base_url).rstrip("/") + path, data=data,
                                         method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
                return response.status, (json.loads(payload) if payload else {})
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                return error.code, (json.loads(payload) if payload else {})
            except ValueError:
                return error.code, {}

    def declared_too_large(self, path: str, length: int, token: str) -> tuple[int, str | None]:
        url = urlsplit(self.base_url)
        conn = http.client.HTTPConnection(url.hostname or "127.0.0.1", url.port or 80, timeout=30)
        try:
            conn.putrequest("POST", path)
            for name, value in (("Authorization", f"Bearer {token}"), ("Content-Type", "application/json"),
                                ("Idempotency-Key", "k-big"), ("Content-Length", str(length))):
                conn.putheader(name, value)
            conn.endheaders()
            response = conn.getresponse()
            payload = response.read()
            code = json.loads(payload).get("code") if payload else None
            return response.status, code
        finally:
            conn.close()

    def start_run(self, token: str, agent: str, *, key: str | None = None,
                  extra: Mapping[str, str] | None = None, **body: Any) -> tuple[int, Json]:
        headers = {"Idempotency-Key": key or self.ids.new_id(IdKind.message), **(extra or {})}
        return self.call("POST", "/v1/runs", token, {"agent": agent, **body}, headers)

    # --- registro de resultados ----------------------------------------------------------------------

    def run(self, name: str, test: Callable[[], str | None]) -> None:
        try:
            detail = test()
            self.results.append(Result(name, "ok", detail or ""))
        except Skip as skip:
            self.results.append(Result(name, "skip", str(skip)))
        except Info as info:
            self.results.append(Result(name, "info", str(info)))
        except AssertionError as failure:
            self.results.append(Result(name, "fail", str(failure)))
        except Exception as error:  # el servidor caído o una respuesta inesperada: falla, sin el texto
            self.results.append(Result(name, "fail", f"{type(error).__name__}"))


class Skip(Exception):
    pass


class Info(Exception):
    """No es una falla: un comportamiento conocido que conviene ver en el reporte."""


def expect(status: int, body: Json, want_status: int, want_code: str | None = None) -> None:
    code = body.get("code")
    assert status == want_status and (want_code is None or code == want_code), (
        f"esperaba {want_status}{' ' + want_code if want_code else ''}, llegó {status} {code or ''} "
        f"(trace_id={body.get('trace_id', '-')})")


# --- nivel 1: plataforma ---------------------------------------------------------------------------------


def platform_checks(c: Check) -> None:
    def health() -> str:
        status, _ = c.call("GET", "/healthz")
        assert status == 200, f"/healthz dio {status}"
        status, ready = c.call("GET", "/readyz")
        assert status == 200, f"/readyz dio {status}: falla {ready.get('failed')}"
        return "healthz y readyz en 200"

    def version() -> str:
        status, body = c.call("GET", "/version")
        assert status == 200, f"/version dio {status}"
        assert body.get("contract") == SCHEMA_VERSION, (
            f"el servidor sirve el contrato {body.get('contract')} y este checkout es {SCHEMA_VERSION}: "
            "¿serve viejo corriendo?")
        return f"contrato {SCHEMA_VERSION}"

    def gateway() -> str:
        if not c.gateway_url:
            raise Skip("sin --gateway-url")
        status, _ = c.call("GET", "/healthz", base=c.gateway_url)
        assert status == 200, f"llm-gateway /healthz dio {status}"
        return "llm-gateway responde"

    def credentials() -> str:
        expect(*c.start_run("no-es-un-jws", "recepcion"), 401, "credentials_invalid")
        expect(*c.call("POST", "/v1/runs", None, {"agent": "recepcion"}, {"Idempotency-Key": "k-1"}),
               401, "credentials_invalid")
        expect(*c.start_run(c.issuer.expired(), "recepcion"), 401, "principal_expired")
        return "firma inválida, ausente y vencida rechazadas"

    def body_limits() -> str:
        # Se declara un Content-Length de 2 MB sin mandar el cuerpo: el servidor tiene que responder 413 sin
        # leerlo. (Mandar el MB de verdad hace que el cierre de la conexión le gane a la lectura del 413.)
        status, code = c.declared_too_large("/v1/runs", 2_000_000, c.issuer.customer(CUSTOMER))
        assert status == 413 and code == "payload_too_large", (
            f"esperaba 413 payload_too_large, llegó {status} {code}")
        long_turn = {"text": "a" * 32_001, "channel": "web", "client_turn_id": "t-1"}
        expect(*c.call("POST", "/v1/sessions/no-existe/turns", c.issuer.customer(CUSTOMER), long_turn),
               422, "invalid_request")
        return "body > 1 MiB → 413; texto > 32 000 → 422"

    state: Json = {}

    def customer_run() -> str:
        token = c.issuer.customer(CUSTOMER)
        key = c.ids.new_id(IdKind.message)
        status, run = c.start_run(token, "recepcion", key=key)
        expect(status, run, 201)
        assert run.get("session_id") and run["first_turn"]["messages"], "el run no trae sesión o saludo"
        state.update(run_id=run["run_id"], key=key)
        status, again = c.start_run(token, "recepcion", key=key)
        assert status in (200, 201) and again.get("run_id") == run["run_id"], "la repetición abrió otro run"
        expect(*c.start_run(token, "consultas", key=key), 409, "idempotency_conflict")
        return f"run {run['run_id']} abierto; idempotencia ok"

    def read_own_and_foreign() -> str:
        if "run_id" not in state:
            raise Skip("no se abrió el run del cliente")
        path = f"/v1/runs/{state['run_id']}"
        status, summary = c.call("GET", path, c.issuer.customer(CUSTOMER))
        expect(status, summary, 200)
        status, body = c.call("GET", path, c.issuer.customer(OTHER_CUSTOMER))
        assert status in (403, 404), f"otro cliente leyó el run: {status}"
        return f"el dueño lo lee ({summary.get('status')}); otro cliente recibe {status} {body.get('code')}"

    def idor() -> str:
        status, body = c.start_run(c.issuer.customer(CUSTOMER), "recepcion",
                                   subject={"kind": "customer", "ref": OTHER_CUSTOMER})
        expect(status, body, 403, "subject_forbidden")
        return "un cliente no abre runs sobre otro cliente"

    def advisor() -> str:
        principal, delegation = c.issuer.advisor(ADVISOR, CUSTOMER)
        status, run = c.start_run(principal, "copiloto-asesor", extra={"X-On-Behalf-Of": delegation},
                                  subject={"kind": "customer", "ref": CUSTOMER})
        expect(status, run, 201)
        other, _ = c.issuer.advisor("adv-8", CUSTOMER)
        expect(*c.start_run(other, "copiloto-asesor", extra={"X-On-Behalf-Of": delegation},
                            subject={"kind": "customer", "ref": CUSTOMER}), 403, "delegation_mismatch")
        return f"copiloto abierto con delegación (run {run['run_id']}); delegación ajena → 403"

    def customer_on_internal_agent() -> str:
        status, body = c.start_run(c.issuer.customer(CUSTOMER), "copiloto-asesor")
        if status == 201:
            raise Info("un cliente abre copiloto-asesor: el authz de demo no aplica invocable_by "
                       "(conocido, runbook §7; con PolicyAuthz se rechaza)")
        return f"rechazado: {status} {body.get('code')}"

    for name, test in [("salud del servidor", health), ("versión del contrato", version),
                       ("llm-gateway arriba", gateway), ("credenciales", credentials),
                       ("topes de tamaño", body_limits), ("run del cliente e idempotencia", customer_run),
                       ("lectura del run (dueño / otro)", read_own_and_foreign), ("IDOR por subject", idor),
                       ("asesor con delegación", advisor),
                       ("cliente en un agente interno", customer_on_internal_agent)]:
        c.run(name, test)


# --- nivel 2: conversaciones (JEV + LLM) -------------------------------------------------------------------


def conversation(c: Check, *, agent: str, lines: Sequence[str], advisor: bool = False,
                 max_follow_ups: int = 0, follow_up: str = "sí") -> tuple[ChatSession, list[str], Json]:
    """Corre una conversación como `testing.chat` (confirma con «sí» y hace step-up si se pide) y devuelve
    la sesión, los mensajes del bot y el resumen final del run."""
    said: list[str] = []
    extra: dict[str, str] = {}
    if advisor:
        principal, delegation = c.issuer.advisor(ADVISOR, CUSTOMER)
        token: Callable[[], str] = lambda: principal  # noqa: E731
        extra = {"X-On-Behalf-Of": delegation}
        step_up = None
    else:
        token = lambda: c.issuer.customer(CUSTOMER)  # noqa: E731
        step_up = lambda: c.issuer.stepped_up(CUSTOMER)  # noqa: E731

    def transport(method: str, path: str, headers: Mapping[str, str], body: Json | None) -> tuple[int, Json]:
        return c.call(method, path, body=body, headers=headers)

    chat = ChatSession(transport=transport, ids=c.ids, token=token, step_up_token=step_up, agent=agent,
                       out=said.append, ask=lambda _: "s", extra_headers=extra)
    chat.start()
    assert chat.run_id is not None, "no se pudo abrir el run"
    for line in lines:
        if chat.closed:
            break
        chat.say(line)
    for _ in range(max_follow_ups):
        if chat.closed:
            break
        chat.say(follow_up)
    headers = {"Authorization": f"Bearer {chat._current_token()}", **extra}
    status, summary = c.call("GET", f"/v1/runs/{chat.run_id}", headers=headers)
    expect(status, summary, 200)
    return chat, said, summary


def conversation_checks(c: Check) -> None:
    if not os.environ.get("AGENTCORE_JEV_API_KEY"):
        reason = ("falta AGENTCORE_JEV_API_KEY: Understand no puede clasificar "
                  "(pon la key en scripts/e2e/.env.e2e)")
        for name in ("A · disputa resuelta", "A · escala por monto", "A · interrupción por fraude",
                     "B · copiloto responde una cifra"):
            c.results.append(Result(name, "skip", reason))
        return

    def resolved() -> str:
        _, _, run = conversation(c, agent="recepcion", max_follow_ups=2,
                                 lines=["no reconozco un cargo en la Tienda Aurora", "el de 120 dólares"])
        assert run.get("outcome") == "resolved", (
            f"terminó {run.get('status')}/{run.get('outcome')} (report.ps1 -Run {run.get('run_id')})")
        return f"resolved (run {run.get('run_id')})"

    def escalated_by_amount() -> str:
        _, _, run = conversation(c, agent="recepcion", max_follow_ups=2,
                                 lines=["no reconozco un cargo de 640 dólares en Electro Norte",
                                        "el de 640 dólares"])
        assert run.get("outcome") == "escalated", (
            f"terminó {run.get('status')}/{run.get('outcome')} (report.ps1 -Run {run.get('run_id')})")
        return f"escalated (run {run.get('run_id')})"

    def fraud() -> str:
        _, _, run = conversation(c, agent="recepcion", lines=["me robaron la tarjeta"])
        assert run.get("outcome") == "escalated", (
            f"terminó {run.get('status')}/{run.get('outcome')} (report.ps1 -Run {run.get('run_id')})")
        return f"escalated (run {run.get('run_id')})"

    def copilot_figure() -> str:
        chat, said, run = conversation(c, agent="copiloto-asesor", advisor=True,
                                       lines=["¿cuánto debe en la tarjeta?"])
        found = amounts(" ".join(said))
        assert Decimal("1342.80") in found, (
            f"la respuesta no trae el saldo de la tarjeta (1342.80); cifras halladas: "
            f"{sorted(str(a) for a in found) or 'ninguna'} (report.ps1 -Run {chat.run_id})")
        return f"cifra validada (run {run.get('run_id')})"

    for name, test in [("A · disputa resuelta", resolved), ("A · escala por monto", escalated_by_amount),
                       ("A · interrupción por fraude", fraud),
                       ("B · copiloto responde una cifra", copilot_figure)]:
        c.run(name, test)


_NUMBER = re.compile(r"\d[\d.,\s\u00a0]*\d|\d")


def amounts(text: str) -> set[Decimal]:
    """Las cifras de un texto en formato es o en (1.342,80 · 1,342.80 · 1 342,80 · 1342.8). Ante la duda se
    agregan las dos lecturas: el check pregunta si la cifra esperada está, no cuál quiso decir el modelo."""
    found: set[Decimal] = set()
    for match in _NUMBER.finditer(text):
        raw = re.sub(r"[\s\u00a0]", "", match.group(0))
        for decimal_mark in (",", "."):
            thousands = "." if decimal_mark == "," else ","
            candidate = raw.replace(thousands, "").replace(decimal_mark, ".")
            try:
                found.add(Decimal(candidate))
            except InvalidOperation:
                continue
    return found


# --- salida ----------------------------------------------------------------------------------------------

_MARK = {"ok": "OK  ", "fail": "FALLA", "skip": "omit", "info": "info"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--gateway-url", default=os.environ.get("AGENTCORE_LLM_GATEWAY_URL"))
    parser.add_argument("--conversations", action="store_true",
                        help="corre también los escenarios con JEV y el LLM reales (cuestan llamadas)")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    check = Check(args.base_url, args.gateway_url)
    platform_checks(check)
    if args.conversations:
        conversation_checks(check)
    width = max(len(r.name) for r in check.results)
    for r in check.results:
        print(f"[{_MARK[r.status]}] {r.name.ljust(width)}  {r.detail}")
    counts = {s: sum(r.status == s for r in check.results) for s in _MARK}
    print(f"\n{counts['ok']} ok · {counts['fail']} fallas · {counts['skip']} omitidas · "
          f"{counts['info']} avisos")
    return 1 if counts["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
