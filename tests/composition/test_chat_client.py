"""`testing.chat`: cliente de conversación sobre la API /v1, probado contra el motor real en memoria."""

from collections.abc import Callable, Mapping
from typing import Any

from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from agent_core.composition.serve import build_api_deps
from testing.chat import ChatSession, repl
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer
from tests.composition.test_serve_app import make_ports


class Console:
    """Captura lo que el chat imprime."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def make_chat(world: EngineWorld, issuer: TestIdentityIssuer, console: Console,
              answers: list[str] | None = None, sent: list[dict[str, Any]] | None = None,
              token: Callable[[], str] | None = None) -> ChatSession:
    client = TestClient(create_app(build_api_deps(make_ports(world, issuer))), raise_server_exceptions=False)

    def transport(method: str, path: str, headers: Mapping[str, str], body: dict[str, Any] | None
                  ) -> tuple[int, dict[str, Any]]:
        if sent is not None and body is not None:
            sent.append(body)
        resp = client.request(method, path, headers=dict(headers), json=body)
        return resp.status_code, resp.json()

    queue = list(answers or [])
    return ChatSession(transport=transport, ids=world.ids, token=token or (lambda: issuer.customer()),
                       agent="atencion", out=console, ask=lambda prompt: queue.pop(0),
                       step_up_token=lambda: issuer.stepped_up())


def test_start_creates_the_run_and_prints_the_first_messages() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    console = Console()
    chat = make_chat(world, issuer, console)

    chat.start()

    assert "¿Qué cargo quieres disputar?" in console.text
    assert chat.run_id is not None


def test_a_confirmation_is_shown_and_answered_with_confirm_instead_of_text() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    console = Console()
    sent: list[dict[str, Any]] = []
    chat = make_chat(world, issuer, console, answers=["no"], sent=sent)
    chat.start()
    world.understands("continue")
    world.matches()

    chat.say("no reconozco un cargo de ciento veinte dólares")

    assert "Voy a radicar la disputa del cargo. ¿Confirmas?" in console.text
    answer = sent[-1]
    assert answer["confirm"]["answer"] == "no"
    assert "text" not in answer


def test_step_up_is_announced_as_simulated_and_the_confirmation_is_retried_with_the_elevated_token() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    console = Console()
    chat = make_chat(world, issuer, console, answers=["sí"])
    chat.start()
    world.understands("continue")
    world.matches()

    chat.say("no reconozco un cargo de ciento veinte dólares")

    assert "simulad" in console.text.lower()
    assert chat.awaiting != "step_up"


def test_an_api_error_is_shown_with_its_code_and_trace_id_and_does_not_crash_the_chat() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    console = Console()
    chat = make_chat(world, issuer, console, token=lambda: "no-es-un-jws")

    chat.start()

    assert "401" in console.text
    assert "trace_id" in console.text
    assert chat.run_id is None


def test_a_closed_run_reports_its_outcome() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    console = Console()
    chat = make_chat(world, issuer, console, answers=["sí"])
    chat.start()
    world.understands("continue")
    world.matches()

    chat.say("no reconozco un cargo de ciento veinte dólares")

    assert chat.closed
    assert "resolved" in console.text


def test_an_expired_credential_is_renewed_and_the_same_client_turn_id_is_retried() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    console = Console()
    sent: list[dict[str, Any]] = []
    expire_next = [False]

    def token() -> str:
        if expire_next[0]:
            expire_next[0] = False
            return issuer.expired()
        return issuer.customer()

    chat = make_chat(world, issuer, console, answers=["no"], sent=sent, token=token)
    chat.start()
    world.understands("continue")
    world.matches()
    expire_next[0] = True

    chat.say("no reconozco un cargo de ciento veinte dólares")

    first, second = [body for body in sent if "text" in body]
    assert first["client_turn_id"] == second["client_turn_id"]
    assert "Voy a radicar la disputa del cargo" in console.text


def test_slash_commands_show_the_run_state_and_the_transcript() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    console = Console()
    chat = make_chat(world, issuer, console)
    chat.start()

    chat.command("/run")
    chat.command("/transcript")

    assert "status=open" in console.text
    assert console.lines.count("¿Qué cargo quieres disputar?") == 2  # una del arranque, otra del transcript


def test_no_credential_is_ever_printed() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    console = Console()
    tokens = [issuer.customer(), issuer.stepped_up()]
    chat = make_chat(world, issuer, console, answers=["sí"])
    chat.start()
    world.understands("continue")
    world.matches()

    chat.say("no reconozco un cargo de ciento veinte dólares")
    chat.command("/transcript")

    assert not any(t in console.text for t in tokens)
    assert "Bearer" not in console.text


def test_the_repl_dispatches_commands_and_stops_on_quit_or_when_the_run_closes() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    console = Console()
    chat = make_chat(world, issuer, console)
    lines = iter(["/run", "/quit", "esto no se lee"])

    repl(chat, read=lambda prompt: next(lines))

    assert "status=open" in console.text
    assert next(lines) == "esto no se lee"
