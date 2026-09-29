"""Paso 13: responder y registrar (transcript en vista `model`, `transcript_fp`)."""

import pytest

from agent_core.domain import (
    Message,
    RejectedDraft,
    ResponseEmitted,
    ResponseEmittedPayload,
    ValidatorOutcome,
)
from agent_core.interpreter import GenerateResult
from tests.m04.harness import RELEASE_ID, RUN_ID, World
from tests.m04.helpers import cmd


def generated(w: World, text: str = "[model]Resumen generado.", *, rejected: bool = False) -> None:
    event = ResponseEmitted(
        event_id="event-resp-1",
        run_id=RUN_ID,
        turn_id="turn-0001",
        release=RELEASE_ID,
        ts=w.clock.now(),
        payload=ResponseEmittedPayload(
            node_id="g", kind="generated", validator=ValidatorOutcome(ok=True), fallback_used=False
        ),
    )
    drafts = [RejectedDraft(text_model="borrador", reason="cifra")] if rejected else []
    message = Message(kind="generated", text=text, locale="es")
    w.responder.push(GenerateResult(message=message, events=[event], rejected=drafts))


def start_generar(w: World) -> None:
    w.open_run()
    w.understand.push(cmd("start_flow", flow="generar"))


def test_el_transcript_recibe_texto_en_vista_model_y_borradores_rechazados() -> None:
    w = World()
    start_generar(w)
    generated(w, rejected=True)
    w.turn("quiero un resumen")
    (call,) = w.recorder.calls
    run_id, turn_id, user_msg, final, rejected = call
    assert (run_id, user_msg, final) == (RUN_ID, "[model]quiero un resumen", "[model]Resumen generado.")
    assert turn_id == "turn-0001" and [d.reason for d in rejected] == ["cifra"]


def test_transcript_fp_se_rellena_en_response_emitted_sin_mutar_los_demas_campos() -> None:
    w = World()
    start_generar(w)
    generated(w)
    w.turn("quiero un resumen")
    (event,) = [e for e in w.events() if e.type == "response_emitted"]
    assert event.payload.transcript_fp is not None and event.payload.transcript_fp.value == "fp1"
    assert event.payload.node_id == "g" and event.payload.kind == "generated"
    assert event.payload.validator.ok is True and event.payload.fallback_used is False


def test_los_mensajes_del_resultado_salen_renderizados_y_el_transcript_en_model() -> None:
    w = World()
    start_generar(w)
    generated(w, "[model]Hola Ana")
    result = w.turn("resumen")
    assert [m.text for m in result.messages] == ["Hola Ana"]  # FakeRuntime.render quita la marca
    assert w.recorder.calls[0][3] == "[model]Hola Ana"


def test_si_el_recorder_falla_el_turno_falla_y_no_persiste_estado() -> None:
    w = World()
    start_generar(w)
    generated(w)
    w.recorder.fail = RuntimeError("transcript no disponible")
    with pytest.raises(RuntimeError):
        w.turn("resumen", client_turn_id="c-1")
    assert w.saved().state_version == 1 and w.saved().active_flow is None
    assert w.store.turn_results == {} and w.store.leases == {}
    assert "response_emitted" not in w.event_types()


def test_turno_sin_response_emitted_no_falla() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"))
    result = w.turn("hola")
    assert result.messages and "response_emitted" not in w.event_types()
    assert len(w.recorder.calls) == 1  # plantillas del motor (C10): solo transcript, sin response_emitted
