"""El artefacto de calibración de `understand-turno` que se versiona sale, sin red, de lo grabado.

Si cambia el conjunto sintético, el modelo o los objetivos, el artefacto y la grabación dejan de coincidir y
esta prueba falla: hay que volver a correr `collect_jev` (con clave) y `calibrate_understand_turno`."""

import json
from pathlib import Path

import pytest

from agent_core.decision.calibration import CalibrationArtifact, DirectoryCalibrationSource, Target, calibrate
from testing.calibration.calibrate_understand_turno import (
    INTERRUPT_NOTE,
    PROVIDER,
    SYNTHETIC_NOTE,
    RecordedJev,
    load_rows,
    model_def,
)
from testing.calibration.understand_turno import examples

DATA = Path(__file__).resolve().parents[2] / "testing" / "calibration" / "data"
RAW = DATA / "understand_turno_raw.jsonl"
ARTIFACTS = sorted(DATA.glob("cal-*.json"))


def _recalibrated() -> CalibrationArtifact:
    dev, test = examples("dev"), examples("test")
    provider = RecordedJev(load_rows(RAW, [*dev, *test]))
    return calibrate(
        model_def(), dev, {PROVIDER: provider},
        targets={"command": Target(metric="precision", value=0.95),
                 "flow": Target(metric="precision", value=0.95),
                 "interrupt": Target(metric="recall", value=0.90)},
        min_samples={"es": 1, "pt": 200}, min_support=10)


def test_there_is_exactly_one_committed_artifact() -> None:
    assert len(ARTIFACTS) == 1


def test_the_committed_artifact_is_what_the_recorded_responses_produce() -> None:
    committed = CalibrationArtifact.from_json(ARTIFACTS[0].read_text(encoding="utf-8"))
    fresh = _recalibrated()
    assert committed.run_id == fresh.run_id and ARTIFACTS[0].stem == fresh.run_id
    assert committed.split_hash == fresh.split_hash
    assert committed.thresholds == fresh.thresholds and committed.calibrators == fresh.calibrators


def test_the_artifact_loads_from_the_directory_and_declares_it_is_synthetic() -> None:
    artifact = DirectoryCalibrationSource(DATA).get(ARTIFACTS[0].stem)
    assert artifact is not None
    assert SYNTHETIC_NOTE in artifact.limitations and INTERRUPT_NOTE in artifact.limitations
    assert any("pt: calibración copiada de es" in item for item in artifact.limitations)


def test_every_command_has_a_threshold_so_none_is_silently_blocked_at_1_0() -> None:
    artifact = CalibrationArtifact.from_json(ARTIFACTS[0].read_text(encoding="utf-8"))
    for command in ("start_flow", "continue", "affirm", "deny", "clarify", "cancel", "handoff",
                    "out_of_scope", "interrupt"):
        assert artifact.threshold("command", command, PROVIDER, "es") < 1.0, command


def test_the_recording_holds_only_model_outputs_and_no_secrets() -> None:
    text = RAW.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert len(rows) == len(examples("dev")) + len(examples("test"))
    allowed = {"id", "split", "lang", "value", "p_raw", "top_k", "model_version", "tokens", "error"}
    assert all(set(row) <= allowed for row in rows)
    assert "api_key" not in text.lower() and "authorization" not in text.lower()


@pytest.mark.parametrize("field", ["command", "flow", "interrupt"])
def test_the_artifact_covers_the_three_calibrated_fields(field: str) -> None:
    artifact = CalibrationArtifact.from_json(ARTIFACTS[0].read_text(encoding="utf-8"))
    assert field in artifact.target
