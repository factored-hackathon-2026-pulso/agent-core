"""Cliente de conversación sobre la API /v1 de `agentcore serve`. Solo para demo y pruebas manuales.

La lógica vive en `ChatSession`, con el transporte, el reloj de IDs y la entrada inyectados; el token nunca
se imprime."""

import argparse
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent_core.adapters.system_clock import SystemClock
from agent_core.adapters.system_ids import SystemIds
from agent_core.ports import IdKind, IdSource
from testing.fakes.identity import TestIdentityIssuer

Transport = Callable[[str, str, Mapping[str, str], dict[str, Any] | None], tuple[int, dict[str, Any]]]


class ChatSession:
    def __init__(self, *, transport: Transport, ids: IdSource, token: Callable[[], str], agent: str,
                 out: Callable[[str], None], ask: Callable[[str], str],
                 step_up_token: Callable[[], str] | None = None) -> None:
        self._transport = transport
        self._ids = ids
        self._token = token
        self._agent = agent
        self._out = out
        self._ask = ask
        self._step_up_token = step_up_token
        self._stepped_up = False
        self.awaiting: str | None = None
        self.closed = False
        self.run_id: str | None = None
        self.session_id: str | None = None

    def start(self) -> None:
        # M0 no tiene un IdKind de petición: `message` sirve de clave de idempotencia.
        headers = {"Authorization": f"Bearer {self._token()}",
                   "Idempotency-Key": self._ids.new_id(IdKind.message)}
        status, body = self._transport("POST", "/v1/runs", headers, {"agent": self._agent})
        if status >= 400:
            self._show_problem(status, body)
            return
        self.run_id = body["run_id"]
        self.session_id = body.get("session_id")
        for message in body["first_turn"]["messages"]:
            self._out(message["text"])

    def say(self, text: str) -> None:
        turn = self._turn({"text": text})
        confirmation = turn.get("confirmation")
        if confirmation is not None:
            self._out(confirmation["action_summary"])
            answer = "yes" if self._ask("¿Confirmas? [s/n] ").strip().lower().startswith(("s", "y")) else "no"
            self._turn({"confirm": {"token": confirmation["token"], "answer": answer}})

    def command(self, line: str) -> None:
        name = line.strip().split()[0]
        paths = {"/run": f"/v1/runs/{self.run_id}", "/transcript": f"/v1/runs/{self.run_id}/transcript"}
        if name not in paths:
            self._out(f"comando desconocido: {name} (usa /run, /transcript, /quit)")
            return
        headers = {"Authorization": f"Bearer {self._current_token()}"}
        status, body = self._transport("GET", paths[name], headers, None)
        if status >= 400:
            self._show_problem(status, body)
        elif name == "/run":
            self._out(f"status={body['status']} outcome={body['outcome']} awaiting={body['awaiting']} "
                      f"handoff={body['handoff_ref']}")
        else:
            for entry in body["entries"]:
                self._out(entry["text"])

    def _current_token(self) -> str:
        return (self._step_up_token if self._stepped_up and self._step_up_token else self._token)()

    def _turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._current_token()}"}
        body = {"channel": "web", "client_turn_id": self._ids.new_id(IdKind.turn), **payload}
        path = f"/v1/sessions/{self.session_id}/turns"
        status, turn = self._transport("POST", path, headers, body)
        if status == 401 and turn.get("code") == "principal_expired":
            # Mismo `client_turn_id`: el servidor procesa el turno una sola vez.
            headers = {"Authorization": f"Bearer {self._current_token()}"}
            status, turn = self._transport("POST", path, headers, body)
        if status >= 400:
            self._show_problem(status, turn)
            return {"messages": []}
        for message in turn["messages"]:
            self._out(message["text"])
        self.awaiting = turn.get("awaiting")
        if turn.get("status") == "closed":
            self.closed = True
            handoff = turn.get("handoff_ref")
            self._out(f"[run cerrado: {turn.get('outcome')}]" + (f" handoff={handoff}" if handoff else ""))
        step_up = turn.get("step_up")
        if step_up is not None and self._step_up_token is not None and not self._stepped_up:
            simulated = " (OTP simulado)" if step_up["simulated"] else ""
            self._out(f"Verificación adicional requerida: {step_up['reason']}{simulated}")
            self._stepped_up = True
            return self._turn(payload)
        return turn

    def _show_problem(self, status: int, problem: dict[str, Any]) -> None:
        self._out(f"error {status} {problem.get('code', '?')}: {problem.get('detail', '')} "
                  f"(trace_id={problem.get('trace_id', '?')})")


def repl(chat: ChatSession, *, read: Callable[[str], str]) -> None:
    """Arranca el run y conversa hasta `/quit`, fin de entrada o cierre del run."""
    chat.start()
    while chat.run_id is not None and not chat.closed:
        try:
            line = read("> ").strip()
        except EOFError:
            return
        if line == "/quit":
            return
        if line.startswith("/"):
            chat.command(line)
        elif line:
            chat.say(line)


def http_transport(base_url: str) -> Transport:
    """Transporte real con la stdlib (sin dependencias nuevas). Solo http(s)."""
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url debe empezar por http:// o https://")

    def send(method: str, path: str, headers: Mapping[str, str], body: dict[str, Any] | None
             ) -> tuple[int, dict[str, Any]]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method,
                                         headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    return send


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chat", description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--agent", default="atencion")
    parser.add_argument("--customer", default="cust-001")
    args = parser.parse_args(argv)
    clock = SystemClock()
    issuer = TestIdentityIssuer(clock)  # claves TEST públicas: el servidor debe correr en demo
    chat = ChatSession(transport=http_transport(args.base_url), ids=SystemIds(),
                       token=lambda: issuer.customer(args.customer),
                       step_up_token=lambda: issuer.stepped_up(args.customer),
                       agent=args.agent, out=print, ask=input)
    repl(chat, read=input)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
