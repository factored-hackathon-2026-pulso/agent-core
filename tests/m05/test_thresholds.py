"""T-M5-01, T-M5-05 y T-M5-06: calibración en runtime y tabla de umbrales."""

from pathlib import Path

import pytest

from agent_core.decision.calibration.artifact import DirectoryCalibrationSource, IsotonicMap
from agent_core.decision.types import DecisionOutput, RawPrediction
from tests.m05.helpers import artifact, identity_map, make_service, model_def, ref

ES_AFFIRM = ("command", "affirm", "classifier", "es")


def _run(*, p_raw: float | None = 0.99, locale: str = "es", art=None, definition=None,  # type: ignore[no-untyped-def]
         value: dict | None = None) -> DecisionOutput:  # type: ignore[type-arg]
    definition = definition or model_def()
    rig = make_service(definition, artifacts=[art] if art is not None else [])
    rig.providers["classifier"].push(
        RawPrediction(value=value or {"command": "affirm"}, p_raw={"command": p_raw}))
    return rig.service.decide_output(ref(definition), {"text": "x"}, locale, rig.vault)  # type: ignore[arg-type]


def _art(threshold: float | None, *, lang: str = "es", cal: IsotonicMap | None = None):  # type: ignore[no-untyped-def]
    thresholds = {} if threshold is None else {("command", "affirm", "classifier", lang): threshold}
    calibrators = {("command", "classifier", lang): cal or identity_map()}
    return artifact(thresholds=thresholds, calibrators=calibrators)


def test_t_m5_01_absent_combination_is_below_threshold() -> None:
    art = artifact(thresholds={("command", "deny", "classifier", "es"): 0.1},
                   calibrators={("command", "classifier", "es"): identity_map()})
    out = _run(p_raw=0.99, art=art)
    assert out.p_cal["command"] == pytest.approx(0.99) and out.above_threshold == {"command": False}


def test_absent_combination_stays_below_even_with_p_cal_one() -> None:
    # Cierra el borde de `p_cal >= 1.0`: sin fila en la tabla nunca pasa (spec §5: "siempre low_confidence").
    out = _run(p_raw=1.0, art=_art(None))
    assert out.above_threshold == {"command": False}


def test_t_m5_05_null_p_raw_is_below_threshold_even_with_zero_threshold() -> None:
    out = _run(p_raw=None, art=_art(0.0))
    assert out.p_cal == {"command": None} and out.above_threshold == {"command": False}


@pytest.mark.parametrize(("locale", "expected"), [("es", True), ("pt", False)])
def test_t_m5_06_threshold_is_per_language(locale: str, expected: bool) -> None:
    art = artifact(
        thresholds={ES_AFFIRM: 0.6, ("command", "affirm", "classifier", "pt"): 0.9},
        calibrators={("command", "classifier", "es"): identity_map(),
                     ("command", "classifier", "pt"): identity_map()})
    out = _run(p_raw=0.8, locale=locale, art=art)
    assert out.p_cal["command"] == pytest.approx(0.8)
    assert out.above_threshold == {"command": expected}


def test_threshold_is_inclusive() -> None:
    assert _run(p_raw=0.6, art=_art(0.6)).above_threshold == {"command": True}


def test_calibrator_is_applied() -> None:
    cal = IsotonicMap(xs=[0.0, 1.0], ys=[0.0, 0.5])
    out = _run(p_raw=0.8, art=_art(0.3, cal=cal))
    assert out.p_cal["command"] == pytest.approx(0.4) and out.p_raw["command"] == pytest.approx(0.8)
    assert out.above_threshold == {"command": True}


def test_method_none_copies_p_raw() -> None:
    definition = model_def(calibration_method="none", calibration_run=None, thresholds_from=None)
    out = _run(p_raw=0.7, definition=definition)
    assert out.p_cal == {"command": pytest.approx(0.7)} and out.above_threshold == {"command": False}


def test_isotonic_without_map_for_the_language_is_null_and_below() -> None:
    out = _run(p_raw=0.9, locale="pt", art=_art(0.1, lang="es"))
    assert out.p_cal == {"command": None} and out.above_threshold == {"command": False}


def test_missing_artifact_means_null_and_all_below() -> None:
    out = _run(p_raw=0.9, art=None)
    assert out.p_cal == {"command": None} and out.above_threshold == {"command": False}


def test_thresholds_from_none_means_all_below() -> None:
    definition = model_def(thresholds_from=None)
    out = _run(p_raw=0.9, art=_art(0.1), definition=definition)
    assert out.p_cal["command"] == pytest.approx(0.9) and out.above_threshold == {"command": False}


def test_calibration_and_thresholds_can_come_from_different_runs() -> None:
    definition = model_def(calibration_run="cal-1", thresholds_from="cal-2")
    rig = make_service(definition, artifacts=[
        artifact(run_id="cal-1", calibrators={("command", "classifier", "es"): identity_map()}),
        artifact(run_id="cal-2", thresholds={ES_AFFIRM: 0.5})])
    rig.providers["classifier"].push(RawPrediction(value={"command": "affirm"}, p_raw={"command": 0.6}))
    out = rig.service.decide_output(ref(definition), {"text": "x"}, "es", rig.vault)
    assert out.above_threshold == {"command": True}


def test_only_calibrated_fields_appear() -> None:
    rig = make_service()
    rig.sources.add(_art(0.1))
    rig.providers["classifier"].push(RawPrediction(
        value={"command": "affirm", "slots": {"a": 1}}, p_raw={"command": 0.9, "slots": 0.9}))
    out = rig.service.decide_output(ref(), {"text": "x"}, "es", rig.vault)
    assert set(out.p_cal) == {"command"} and set(out.above_threshold) == {"command"}


def test_calibrated_field_absent_from_value_is_below() -> None:
    definition = model_def(calibrated=("command", "flow"))
    art = artifact(thresholds={ES_AFFIRM: 0.1},
                   calibrators={("command", "classifier", "es"): identity_map(),
                                ("flow", "classifier", "es"): identity_map()})
    out = _run(p_raw=0.9, art=art, definition=definition)
    assert out.above_threshold == {"command": True, "flow": False}
    assert out.p_cal["flow"] is None


def test_directory_source_reads_by_run_id(tmp_path: Path) -> None:
    art = _art(0.6)
    (tmp_path / "cal-1.json").write_text(art.to_json(), encoding="utf-8")
    source = DirectoryCalibrationSource(tmp_path)
    assert source.get("cal-1") == art
    assert source.get("cal-404") is None


@pytest.mark.parametrize("run_id", ["../cal-1", "a/b", ""])
def test_directory_source_rejects_paths_in_run_id(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ValueError):
        DirectoryCalibrationSource(tmp_path).get(run_id)
