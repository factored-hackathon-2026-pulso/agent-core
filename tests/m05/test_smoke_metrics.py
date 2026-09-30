"""Métricas puras de la prueba de humo de JEV (`tests/m05/smoke`). Offline y sintéticas."""

import pytest

from tests.m05.smoke.metrics import Bin, accuracy, bucket, nearest_rank, reliability, word_count


def test_nearest_rank_matches_the_spec_definition() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    assert nearest_rank(values, 50) == 50.0
    assert nearest_rank(values, 95) == 100.0
    assert nearest_rank(values, 10) == 10.0
    assert nearest_rank(list(reversed(values)), 50) == 50.0  # no depende del orden de entrada
    assert nearest_rank([7.0], 95) == 7.0
    assert nearest_rank([], 50) is None


def test_accuracy_handles_empty() -> None:
    assert accuracy([True, True, False, True]) == 0.75
    assert accuracy([]) is None


def test_reliability_bins_and_edges() -> None:
    pairs = [(0.30, False), (0.49, True), (0.50, True), (0.65, False), (0.95, True), (0.95, True),
             (1.0, True)]
    bins = reliability(pairs)
    assert [(b.lo, b.hi, b.n) for b in bins] == [(0.0, 0.5, 2), (0.5, 0.7, 2), (0.7, 0.9, 0), (0.9, 1.0, 3)]
    assert bins[0] == Bin(lo=0.0, hi=0.5, n=2, mean_p=pytest.approx(0.395), accuracy=0.5)  # type: ignore[arg-type]
    assert bins[2].mean_p is None and bins[2].accuracy is None
    assert bins[3].accuracy == 1.0  # p == 1.0 cae en el último bin


@pytest.mark.parametrize(("text", "words"), [
    ("Sí", 1), ("no gracias", 2), ("eh… no sé", 3), ("  ", 0), ("¿Dónde está mi pedido?", 4), ("…", 0),
])
def test_word_count_counts_words_with_letters(text: str, words: int) -> None:
    assert word_count(text) == words


@pytest.mark.parametrize(("words", "label"), [
    (1, "1"), (2, "2-3"), (3, "2-3"), (4, "4-7"), (7, "4-7"), (8, "8+")])
def test_length_buckets(words: int, label: str) -> None:
    assert bucket(words) == label
