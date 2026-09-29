import pytest
from hypothesis import given
from hypothesis import strategies as st

from agent_core.decision.calibration.isotonic import fit_isotonic


def _fit(points: list[tuple[float, int]]):  # type: ignore[no-untyped-def]
    return fit_isotonic([(p, float(y), f"id-{i:03d}") for i, (p, y) in enumerate(points)])


def test_already_monotone_points_stay_as_they_are() -> None:
    m = _fit([(0.1, 0), (0.4, 1), (0.35, 0)])  # ordenados: (0.1,0) (0.35,0) (0.4,1)
    assert m is not None and m.xs == [0.1, 0.35, 0.4] and m.ys == [0.0, 0.0, 1.0]


def test_violation_is_pooled_by_average_known_case() -> None:
    m = _fit([(0.2, 1), (0.5, 0), (0.6, 1)])
    assert m is not None
    assert m.xs == [0.2, 0.5, 0.6] and m.ys == pytest.approx([0.5, 0.5, 1.0])


def test_ties_in_x_are_merged_before_pooling() -> None:
    m = _fit([(0.3, 1), (0.3, 0), (0.3, 0), (0.8, 1)])
    assert m is not None
    assert m.xs == [0.3, 0.8] and m.ys == pytest.approx([1 / 3, 1.0])


def test_full_pool_when_labels_are_reversed() -> None:
    m = _fit([(0.1, 1), (0.5, 1), (0.9, 0), (0.95, 0)])
    assert m is not None and m.xs == [0.1, 0.95] and m.ys == pytest.approx([0.5, 0.5])


def test_single_point_and_empty() -> None:
    m = _fit([(0.7, 1)])
    assert m is not None and m.xs == [0.7] and m.ys == [1.0]
    assert fit_isotonic([]) is None


def test_result_does_not_depend_on_input_order() -> None:
    points = [(0.2, 1.0, "a"), (0.5, 0.0, "b"), (0.5, 1.0, "c"), (0.6, 1.0, "d"), (0.1, 0.0, "e")]
    assert fit_isotonic(points) == fit_isotonic(list(reversed(points)))


@given(st.lists(st.tuples(st.floats(0, 1), st.integers(0, 1)), min_size=1, max_size=40))
def test_fitted_map_is_monotone_and_bounded(points: list[tuple[float, int]]) -> None:
    m = fit_isotonic([(p, float(y), f"id-{i:03d}") for i, (p, y) in enumerate(points)])
    assert m is not None
    assert m.ys == sorted(m.ys) and all(0.0 <= y <= 1.0 for y in m.ys)
    assert all(m.apply(a) <= m.apply(b) for a, b in zip(m.xs, m.xs[1:], strict=False))
