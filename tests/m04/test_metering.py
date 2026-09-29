"""Medición por etapas de M4 (m04 §3.7)."""

from datetime import timedelta

import pytest

from agent_core.turn.metering import StageMeter
from testing.fakes.clock import FakeClock


def test_las_etapas_acumulan_y_las_no_corridas_son_none() -> None:
    clock = FakeClock()
    meter = StageMeter(clock)
    with meter.stage("guards"):
        clock.advance(timedelta(milliseconds=2))
    with meter.stage("flow"):
        clock.advance(timedelta(milliseconds=10))
    with meter.stage("flow"):
        clock.advance(timedelta(milliseconds=5))
    stages = meter.stages()
    assert (stages.guards_ms, stages.flow_ms) == (2, 15)
    assert stages.understand_ms is None and stages.response_ms is None


def test_duration_desde_la_recepcion() -> None:
    clock = FakeClock()
    clock.advance(timedelta(seconds=5))
    meter = StageMeter(clock)
    clock.advance(timedelta(milliseconds=87))
    assert meter.duration_ms() == 87


def test_una_etapa_que_falla_igual_queda_medida() -> None:
    clock = FakeClock()
    meter = StageMeter(clock)
    with pytest.raises(RuntimeError), meter.stage("understand"):
        clock.advance(timedelta(milliseconds=3))
        raise RuntimeError("boom")
    assert meter.stages().understand_ms == 3


def test_ms_enteros_por_division_entera() -> None:
    clock = FakeClock()
    meter = StageMeter(clock)
    with meter.stage("response"):
        clock.advance(timedelta(microseconds=1999))
    assert meter.stages().response_ms == 1


def test_etapa_desconocida_es_error() -> None:
    meter = StageMeter(FakeClock())
    with pytest.raises(ValueError), meter.stage("otra"):  # type: ignore[arg-type]
        pass
