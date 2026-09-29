"""PII (regla dura 6): el texto crudo del usuario solo llega a la UoW como valor de slot del run."""

from decimal import Decimal
from typing import Any

import pytest

from agent_core.domain import dumps, to_jsonable
from tests.m04.harness import World
from tests.m04.helpers import FakeRuntime, cmd

EMAIL = "user@example.test"
M4_EVENTS = {
    "run_started",
    "turn_started",
    "command_emitted",
    "expiry_evaluated",
    "turn_completed",
    "run_closed",
}


@pytest.fixture(autouse=True)
def tokenizing_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un M7 que sí tokeniza: el texto en vista `model` no contiene el dato crudo."""
    monkeypatch.setattr(FakeRuntime, "model_text", lambda self, text: "⟦tok:pii-1⟧")


def blob(value: Any) -> str:
    return dumps(to_jsonable(value))


def test_ningun_evento_ni_outbox_ni_resultado_contiene_el_texto_crudo() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("continue"), cmd("handoff"))
    first = w.turn(f"mi correo es {EMAIL}")
    second = w.turn(f"escríbanme a {EMAIL}")
    assert EMAIL in blob(w.saved().slots["descripcion"])  # el slot del run sí lo guarda (vista full)
    everything = [blob(e) for e in w.events()] + [blob(m) for m in w.outbox().pending(10)]
    everything += [blob(first), blob(second), blob(w.recorder.calls), blob(w.guards.calls)]
    everything += [blob(c.text_model) for c in w.understand.calls]
    assert not [text for text in everything if EMAIL in text]


def test_los_eventos_de_m4_no_llevan_texto_de_usuario() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"))
    w.turn(f"hola {EMAIL}")
    m4 = [e for e in w.events() if e.type in M4_EVENTS]
    assert {e.type for e in m4} >= {"turn_started", "expiry_evaluated", "command_emitted", "turn_completed"}
    assert not [e for e in m4 if EMAIL in blob(e)]


def test_las_guardas_y_understand_solo_ven_la_vista_model() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"))
    w.turn(f"hola {EMAIL}")
    assert w.guards.calls == ["⟦tok:pii-1⟧"] and w.understand.calls[0].text_model == "⟦tok:pii-1⟧"
    assert w.recorder.calls[0][2] == "⟦tok:pii-1⟧"


def test_no_hay_float_en_el_costo() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope", cost="0.001"))
    w.turn("hola")
    costs = [c for entries in w.store.usage.values() for _, c in entries]
    assert costs and all(isinstance(c, Decimal) for c in costs)
