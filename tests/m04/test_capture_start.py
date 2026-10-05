"""M2 D16: el texto que arranca el flow responde a los `collect` de entrada con `capture_start`."""

from agent_core.domain import Awaiting
from tests.m04.harness import World, flow, node
from tests.m04.helpers import cmd

AGENT = r"^\s*(?i:agente\s*:\s*)?([a-z0-9][a-z0-9_-]{0,63})\s*(?:[.;,\n]|$)"
GOAL = r"^(?:.*?(?i:objetivo)\s*:\s*)?(?!(?i:agente)\s*:\s*[a-z0-9_-]+\s*[.;,]?\s*$)(\S.*)$"

CONSTRUIR = flow(
    "construir", 50,
    node("pedir_agente", "collect", {"slot": "agente", "prompt_ref": "t-pedir@1.0.0", "capture_start": True,
                                     "validator": {"kind": "extract", "value": AGENT}}, ok="pedir_objetivo"),
    node("pedir_objetivo", "collect", {"slot": "objetivo", "prompt_ref": "t-pedir-tarjeta@1.0.0",
                                       "capture_start": True,
                                       "validator": {"kind": "extract", "value": GOAL}}, ok="fin"),
    node("fin", "end", {"outcome": "resolved"}),
)


def _start(text: str) -> tuple[World, object]:
    w = World(extra=(CONSTRUIR,))
    w.open_run()
    w.understand.push(cmd("start_flow", flow="construir"))
    return w, w.turn(text)


def test_one_message_fills_both_slots_and_runs_on() -> None:
    w, _ = _start("Agente: cobros. Objetivo: un agente nuevo para los casos de cobro indebido")
    state = w.saved()
    assert (state.slots["agente"].value, state.slots["objetivo"].value) == (
        "cobros", "un agente nuevo para los casos de cobro indebido")
    assert state.slots["agente"].status == "validated" and state.active_flow is None  # llegó al fin


def test_a_message_without_the_format_asks_instead_of_failing() -> None:
    w, result = _start("quiero cambiar el agente de cobros por favor")
    state = w.saved()
    assert "agente" not in state.slots and state.awaiting is Awaiting.slot
    assert [m.text for m in result.messages] == [w.text("t-pedir")]  # pregunta, sin gastar intentos
    assert state.node_attempts.get("pedir_agente", 0) == 0


def test_only_the_agent_given_asks_for_the_goal() -> None:
    w, result = _start("Agente: cobros")
    state = w.saved()
    assert state.slots["agente"].value == "cobros" and "objetivo" not in state.slots
    assert state.awaiting_node_id == "pedir_objetivo"
    assert [m.text for m in result.messages] == [w.text("t-pedir-tarjeta")]


def test_a_later_plain_answer_still_validates() -> None:
    w, _ = _start("quiero un agente")
    w.understand.push(cmd("continue"))
    w.turn("cobros")
    assert w.saved().slots["agente"].value == "cobros"
