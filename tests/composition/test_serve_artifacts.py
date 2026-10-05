"""Calibración y proveedor `classifier` desde directorios (archivos sintéticos; sin dataset)."""

import json
from pathlib import Path

import pytest

from agent_core.composition.artifacts import (
    CALIBRATION_DIR_ENV,
    CLASSIFIER_ARTIFACTS_DIR_ENV,
    DirectoryArtifactLoader,
    calibration_from_env,
    classifier_from_env,
)
from agent_core.composition.transcript import transcript
from agent_core.domain import ProviderSpec, SchemaError

SYNTHETIC_CLASSIFIER = {
    "format": "tfidf-logreg-v1", "data_hash": "a" * 64, "vocab": {"hola": 0, "adios": 1}, "idf": [1.0, 1.0],
    "classes": {"intent": ["saludo", "despedida"]},
    "coef": {"intent": [[2.0, -2.0], [-2.0, 2.0]]}, "intercept": {"intent": [0.0, 0.0]},
}
SYNTHETIC_CALIBRATION = {"run_id": "cal-1", "split_hash": "b" * 64, "method": "none", "calibrators": [],
                         "thresholds": [{"key": ["intent", "saludo", "classifier", "es"], "value": 0.6}],
                         "target": {}, "limitations": [], "metrics": {}}


def test_a_missing_or_wrong_directory_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match=CALIBRATION_DIR_ENV):
        calibration_from_env({})
    with pytest.raises(SchemaError, match=CLASSIFIER_ARTIFACTS_DIR_ENV):
        classifier_from_env({CLASSIFIER_ARTIFACTS_DIR_ENV: str(tmp_path / "no-existe")})


def test_calibration_is_read_by_run_id(tmp_path: Path) -> None:
    (tmp_path / "cal-1.json").write_text(json.dumps(SYNTHETIC_CALIBRATION), encoding="utf-8")

    source = calibration_from_env({CALIBRATION_DIR_ENV: str(tmp_path)})

    artifact = source.get("cal-1")
    assert artifact is not None and artifact.threshold("intent", "saludo", "classifier", "es") == 0.6
    assert source.get("otro") is None


def test_the_classifier_provider_loads_its_artifact_from_the_directory(tmp_path: Path) -> None:
    (tmp_path / "intents-v1.json").write_text(json.dumps(SYNTHETIC_CLASSIFIER), encoding="utf-8")
    provider = classifier_from_env({CLASSIFIER_ARTIFACTS_DIR_ENV: str(tmp_path)})
    spec = ProviderSpec.model_validate({"provider": "classifier", "config": {"artifact": "intents-v1"}})

    prediction = provider.predict(spec, {"text": "hola hola"}, {}, "es")

    assert prediction.value == {"intent": "saludo"}


@pytest.mark.parametrize("ref", ["", "../secreto", "a/b", "a\\b", ".oculto"])
def test_the_loader_refuses_references_that_leave_the_directory(tmp_path: Path, ref: str) -> None:
    with pytest.raises(ValueError):
        DirectoryArtifactLoader(tmp_path).load(ref)


def test_the_transcript_factory_needs_the_engine_store() -> None:
    from agent_core.composition.serve_ports import DemoContext

    with pytest.raises(SchemaError):
        transcript(DemoContext(clock=None, ids=None, registry=None))  # type: ignore[arg-type]
