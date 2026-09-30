"""Fase B: `TurnRecorder` y `AuditLog` reales (M11) cableados como `recorder` y `chain` de M4."""

from typing import TYPE_CHECKING

from agent_core.audit import AuditLog, TurnRecorder
from agent_core.domain import ResponseEmitted
from agent_core.turn import TurnEngine
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.transcript import InMemoryTranscript
from tests.m04.harness import RUN_ID, World
from tests.m04.test_record import generated, start_generar

if TYPE_CHECKING:
    from agent_core.turn import EventChain, TurnRecorderPort

    def _conforms(recorder: TurnRecorder, log: AuditLog) -> tuple[TurnRecorderPort, EventChain]:
        return recorder, log


def _real_world() -> tuple[World, InMemoryTranscript]:
    w = World()
    transcript = InMemoryTranscript()
    w.recorder = TurnRecorder(transcript, FakeKeyProvider.default())  # type: ignore[assignment]
    w.chain = AuditLog(w.audit)  # type: ignore[assignment]
    w.engine = TurnEngine(**w.engine_kwargs())
    return w, transcript


def test_transcript_fp_es_la_huella_de_la_respuesta_final_aunque_haya_borradores_rechazados() -> None:
    """M11 devuelve `[user, *rejected, final]` (m11): la huella es la de la última."""
    w, transcript = _real_world()
    start_generar(w)
    generated(w, "[model]Resumen generado.", rejected=True)
    w.turn("quiero un resumen")
    expected = TurnRecorder(InMemoryTranscript(), FakeKeyProvider.default()).record_turn(
        RUN_ID, "turn-0001", "x", "[model]Resumen generado.", [])[1].fingerprint
    (event,) = [e for e in w.events() if isinstance(e, ResponseEmitted)]
    assert event.payload.transcript_fp == expected
    assert [e.role for e in transcript.read(RUN_ID)] == ["user", "rejected_draft", "assistant"]


def test_los_eventos_del_turno_quedan_encadenados_por_el_audit_log_real() -> None:
    w, _ = _real_world()
    start_generar(w)
    generated(w)
    w.turn("quiero un resumen")
    check = AuditLog(w.audit).verify_chain(RUN_ID)
    assert check.ok, check.reason
