from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from agent_core.decision.calibration.artifact import CalibrationArtifact, IsotonicMap, Target

FIXTURE = Path(__file__).parent / "fixtures" / "calibration_small.json"
HASH = "a" * 64


def _artifact(**over: object) -> CalibrationArtifact:
    base: dict[str, object] = dict(
        run_id="cal-1", split_hash=HASH, method="isotonic",
        calibrators={("command", "classifier", "es"): IsotonicMap(xs=[0.2, 0.8], ys=[0.1, 0.9])},
        thresholds={("command", "affirm", "classifier", "es"): 0.6},
        target={"command": Target(metric="precision", value=0.9)},
    )
    return CalibrationArtifact(**{**base, **over})  # type: ignore[arg-type]


def test_absent_threshold_is_one() -> None:
    art = _artifact()
    assert art.threshold("command", "affirm", "classifier", "es") == pytest.approx(0.6)
    assert art.threshold("command", "affirm", "classifier", "pt") == 1.0
    assert art.threshold("command", "deny", "classifier", "es") == 1.0


def test_calibrator_lookup() -> None:
    art = _artifact()
    assert art.calibrator("command", "classifier", "es") is not None
    assert art.calibrator("command", "classifier", "pt") is None


def test_round_trip() -> None:
    art = _artifact(limitations=["pt: calibración copiada de es"], metrics={"n": 3})
    assert CalibrationArtifact.from_json(art.to_json()) == art


def test_to_json_is_deterministic_regardless_of_insertion_order() -> None:
    a = IsotonicMap(xs=[0.2, 0.8], ys=[0.1, 0.9])
    first = _artifact(calibrators={("a", "p", "es"): a, ("b", "p", "es"): a},
                      thresholds={("a", "x", "p", "es"): 0.5, ("b", "y", "p", "pt"): 0.4})
    second = _artifact(calibrators={("b", "p", "es"): a, ("a", "p", "es"): a},
                       thresholds={("b", "y", "p", "pt"): 0.4, ("a", "x", "p", "es"): 0.5})
    assert first.to_json() == second.to_json()


def test_values_with_pipe_or_separator_do_not_collide() -> None:
    art = _artifact(thresholds={("f", "a|b", "p", "es"): 0.5, ("f", "a", "b|p", "es"): 0.6})
    back = CalibrationArtifact.from_json(art.to_json())
    assert back.threshold("f", "a|b", "p", "es") == pytest.approx(0.5)
    assert back.threshold("f", "a", "b|p", "es") == pytest.approx(0.6)


def test_fixture_loads() -> None:
    art = CalibrationArtifact.from_json(FIXTURE.read_text(encoding="utf-8"))
    assert len(art.split_hash) == 64
    assert art.threshold("command", "affirm", "classifier", "es") == pytest.approx(0.6)
    assert art.threshold("command", "affirm", "classifier", "pt") == pytest.approx(0.9)
    assert art.target["interrupt"].metric == "recall"
    assert art.limitations
    assert CalibrationArtifact.from_json(art.to_json()) == art


def test_from_json_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        CalibrationArtifact.from_json("no es json")
    with pytest.raises(ValueError):
        CalibrationArtifact.from_json("[1]")


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan")])
def test_threshold_out_of_range_is_rejected(threshold: float) -> None:
    with pytest.raises(ValidationError):
        _artifact(thresholds={("command", "affirm", "classifier", "es"): threshold})


def test_split_hash_must_be_sha256_hex() -> None:
    with pytest.raises(ValidationError):
        _artifact(split_hash="abc")


def test_target_value_in_unit_interval() -> None:
    with pytest.raises(ValidationError):
        Target(metric="recall", value=1.5)


@pytest.mark.parametrize(("xs", "ys"), [
    ([0.5, 0.5], [0.1, 0.2]),                # xs no estrictamente creciente
    ([0.6, 0.4], [0.1, 0.2]),
    ([0.2, 0.8], [0.9, 0.1]),                # ys decreciente
    ([0.2, float("nan")], [0.1, 0.2]),
    ([0.2, float("inf")], [0.1, 0.2]),
    ([0.2, 0.8], [0.1, float("nan")]),
    ([0.2, 0.8], [0.1]),                     # largos distintos
    ([], []),
    ([0.2, 0.8], [0.1, 1.5]),                # fuera de [0, 1]
])
def test_isotonic_map_rejects_invalid_points(xs: list[float], ys: list[float]) -> None:
    with pytest.raises(ValidationError):
        IsotonicMap(xs=xs, ys=ys)


def test_isotonic_apply_known_points() -> None:
    m = IsotonicMap(xs=[0.2, 0.8], ys=[0.1, 0.9])
    assert m.apply(0.0) == pytest.approx(0.1)   # antes del primer punto: constante
    assert m.apply(0.2) == pytest.approx(0.1)
    assert m.apply(0.5) == pytest.approx(0.5)   # interpolación lineal
    assert m.apply(0.8) == pytest.approx(0.9)
    assert m.apply(1.0) == pytest.approx(0.9)
    assert IsotonicMap(xs=[0.5], ys=[0.4]).apply(0.9) == pytest.approx(0.4)


_MAP = IsotonicMap(xs=[0.1, 0.3, 0.6, 0.9], ys=[0.0, 0.2, 0.2, 1.0])


@given(st.floats(0, 1), st.floats(0, 1))
def test_isotonic_apply_is_monotone_and_bounded(p1: float, p2: float) -> None:
    low, high = sorted((p1, p2))
    assert _MAP.apply(low) <= _MAP.apply(high)
    assert 0.0 <= _MAP.apply(low) <= 1.0
