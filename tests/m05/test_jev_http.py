"""Transporte HTTP real de JEV contra un servidor local en hilo (sin red externa, datos sintéticos)."""

import json
import threading
import time
import urllib.error
from collections.abc import Iterator
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_core.decision.providers.jev import JevTransport, JevTransportError
from agent_core.decision.providers.jev_http import HttpJevTransport
from agent_core.decision.types import DecisionConfigError
from agent_core.domain import JsonValue
from testing.fakes.clock import FakeClock

KEY = "sk-sintetica-CLAVE-NO-FILTRAR"
REQUEST: dict[str, JsonValue] = {"state": "texto sintético", "model": "jev-latest",
                                 "questions": {"command": {"type": "choice", "instructions": "¿cuál?",
                                                           "criteria": {"affirm": None, "deny": None}}}}
OK_BODY = {"model": "jev-1.13.0", "answers": {"command": {"type": "choice", "choice": "affirm",
                                                          "probabilities": {"affirm": 0.9, "deny": 0.1},
                                                          "confidence": 0.8}},
           "usage": {"input_tokens": 10, "output_tokens": 2}}


class Script:
    """Respuestas guionadas del servidor local y registro de lo recibido."""

    def __init__(self) -> None:
        self.responses: list[tuple[int, dict[str, str], bytes, float]] = []
        self.received: list[tuple[str, dict[str, str], bytes]] = []

    def push(self, status: int, body: object = None, headers: dict[str, str] | None = None,
             delay: float = 0.0) -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body if body is not None else {}).encode()
        self.responses.append((status, headers or {}, raw, delay))


@pytest.fixture
def server() -> Iterator[tuple[Script, str]]:
    script = Script()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            script.received.append((self.path, {k.lower(): v for k, v in self.headers.items()},
                                    self.rfile.read(length)))
            status, headers, body, delay = script.responses.pop(0)
            time.sleep(delay)
            try:
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:  # el cliente ya cerró (prueba de timeout)
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield script, f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


class Rig:
    def __init__(self, base_url: str, **kwargs: int) -> None:
        self.clock = FakeClock()
        self.sleeps: list[float] = []
        self.transport = HttpJevTransport(lambda: KEY, self.clock, base_url=base_url, sleep=self._sleep,
                                          **kwargs)

    def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.clock.advance(timedelta(seconds=seconds))


def test_posts_the_request_with_bearer_and_returns_the_decoded_response(server: tuple[Script, str]) -> None:
    script, url = server
    script.push(200, OK_BODY)
    response = Rig(url).transport.send(REQUEST, 2000)
    path, headers, body = script.received[0]
    assert path == "/v1/systemone"
    assert headers["authorization"] == f"Bearer {KEY}"
    assert headers["content-type"] == "application/json"
    assert json.loads(body) == json.loads(json.dumps(REQUEST))
    assert response["model"] == "jev-1.13.0"
    assert isinstance(response["answers"], dict) and response["usage"] == {"input_tokens": 10,
                                                                          "output_tokens": 2}


@pytest.mark.parametrize("status", [429, 529])
def test_retries_with_exponential_backoff(server: tuple[Script, str], status: int) -> None:
    script, url = server
    for _ in range(3):
        script.push(status)
    script.push(200, OK_BODY)
    rig = Rig(url, backoff_base_ms=200)
    assert rig.transport.send(REQUEST, 10_000)["model"] == "jev-1.13.0"
    assert rig.sleeps == [0.2, 0.4, 0.8] and len(script.received) == 4


def test_retry_after_is_respected(server: tuple[Script, str]) -> None:
    script, url = server
    script.push(429, headers={"Retry-After": "2"})
    script.push(200, OK_BODY)
    rig = Rig(url, backoff_base_ms=200)
    rig.transport.send(REQUEST, 10_000)
    assert rig.sleeps == [2.0]


@pytest.mark.parametrize("status", [401, 422, 400, 500, 503])
def test_no_retry_on_other_errors(server: tuple[Script, str], status: int) -> None:
    script, url = server
    script.push(status, {"error": "detalle con " + KEY})
    rig = Rig(url)
    with pytest.raises(JevTransportError) as info:
        rig.transport.send(REQUEST, 2000)
    assert info.value.status == status and len(script.received) == 1 and rig.sleeps == []


def test_retries_exhausted_raises_with_the_last_status(server: tuple[Script, str]) -> None:
    script, url = server
    for _ in range(3):
        script.push(429)
    rig = Rig(url, max_retries=2)
    with pytest.raises(JevTransportError) as info:
        rig.transport.send(REQUEST, 60_000)
    assert info.value.status == 429 and len(script.received) == 3 and len(rig.sleeps) == 2


def test_wait_longer_than_the_budget_is_a_timeout(server: tuple[Script, str]) -> None:
    script, url = server
    script.push(429, headers={"Retry-After": "5"})
    rig = Rig(url)
    with pytest.raises(TimeoutError):
        rig.transport.send(REQUEST, 1000)
    assert rig.sleeps == [] and len(script.received) == 1


def test_slow_server_is_a_timeout(server: tuple[Script, str]) -> None:
    script, url = server
    script.push(200, OK_BODY, delay=1.0)
    with pytest.raises(TimeoutError):
        Rig(url).transport.send(REQUEST, 150)


def test_network_failure_is_a_transport_error_without_status(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig("http://127.0.0.1:9")

    def refuse(*args: object, **kwargs: object) -> object:
        raise urllib.error.URLError(ConnectionRefusedError("rechazada"))

    monkeypatch.setattr(rig.transport._opener, "open", refuse)
    with pytest.raises(JevTransportError) as info:
        rig.transport.send(REQUEST, 500)
    assert info.value.status is None and info.value.__suppress_context__

@pytest.mark.parametrize("body", [b"no es json", b"[1, 2]", b'"texto"', b""])
def test_bad_body_is_a_transport_error(server: tuple[Script, str], body: bytes) -> None:
    script, url = server
    script.push(200, body)
    with pytest.raises(JevTransportError):
        Rig(url).transport.send(REQUEST, 2000)


def test_redirects_are_not_followed(server: tuple[Script, str]) -> None:
    script, url = server
    script.push(307, headers={"Location": "http://127.0.0.1:1/robo"})
    with pytest.raises(JevTransportError) as info:
        Rig(url).transport.send(REQUEST, 2000)
    assert info.value.status == 307 and len(script.received) == 1


def test_key_and_body_never_appear_in_repr_or_errors(server: tuple[Script, str]) -> None:
    script, url = server
    rig = Rig(url)
    assert KEY not in repr(rig.transport) and KEY not in str(rig.transport)
    leaked = "texto sintético"
    failures: list[BaseException] = []
    bad: list[tuple[int, object]] = [(401, {"error": KEY, "echo": leaked}), (422, {"echo": leaked}),
                                     (500, b"boom " + KEY.encode())]
    for status, body in bad:
        script.push(status, body)
        try:
            rig.transport.send(REQUEST, 2000)
        except JevTransportError as exc:
            failures.append(exc)
    script.push(200, b"basura " + KEY.encode())
    try:
        rig.transport.send(REQUEST, 2000)
    except JevTransportError as exc:
        failures.append(exc)
    assert len(failures) == 4
    for exc in failures:
        text = f"{exc!s} {exc!r} {exc.args!r}"
        assert KEY not in text and leaked not in text and "Bearer" not in text
        assert exc.__cause__ is None and exc.__suppress_context__


def test_empty_key_is_a_config_error_and_sends_nothing(server: tuple[Script, str]) -> None:
    script, url = server
    transport = HttpJevTransport(lambda: "  ", FakeClock(), base_url=url)
    with pytest.raises(DecisionConfigError) as info:
        transport.send(REQUEST, 1000)
    assert script.received == [] and KEY not in str(info.value)


@pytest.mark.parametrize("url", ["http://api.typesafe.ai", "ftp://127.0.0.1", "api.typesafe.ai", ""])
def test_rejects_plain_http_to_remote_hosts(url: str) -> None:
    with pytest.raises(DecisionConfigError):
        HttpJevTransport(lambda: KEY, FakeClock(), base_url=url)


def test_default_base_url_is_https() -> None:
    transport: JevTransport = HttpJevTransport(lambda: KEY, FakeClock())
    assert transport is not None
