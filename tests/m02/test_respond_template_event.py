"""D6 (2026-09-30): M2 emite `response_emitted` (`kind=template`) al renderizar una plantilla."""

from agent_core.domain import ResponseEmitted
from tests.m02.harness import World, flow, template
from tests.m02.test_respond_generate import FIN, _state, _world

TPL = {"id": "r", "type": "respond", "next": {"next": "fin"}, "config": {"template_ref": "t/hola@1.0.0"}}


def _emitted(out):  # type: ignore[no-untyped-def]
    return [e for e in out.events if isinstance(e, ResponseEmitted)]


def test_template_respond_emits_response_emitted_with_kind_template() -> None:
    w = World()
    w.add(template("t/hola", "Hola"))
    out = w.step(w.state(flow(TPL, FIN)))
    (event,) = _emitted(out)
    p = event.payload
    assert (p.kind, p.node_id, p.fallback_used, p.llm) == ("template", "r", False, None)
    assert p.validator.ok and p.validator.failures == [] and p.validator.regenerations == 0
    assert p.transcript_fp is None  # lo rellena M4 tras `record_turn`


def test_degraded_generate_emits_template_event_with_fallback_used() -> None:
    w = _world()
    out = w.step(_state(w), degraded=True)
    (event,) = _emitted(out)
    assert (event.payload.kind, event.payload.fallback_used, event.payload.llm) == ("template", True, None)


def test_generate_path_does_not_duplicate_the_event() -> None:
    from agent_core.domain import Message
    from agent_core.interpreter import GenerateResult

    w = _world()
    w.responder.push(GenerateResult(message=Message(kind="generated", text="ok", locale="es")))
    out = w.step(_state(w))
    assert _emitted(out) == []  # M8 lo emite (llega en `result.events`); el doble no trae ninguno
