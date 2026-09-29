"""Wrapper de lingua (M6 §3.1.4, §5)."""

import importlib.metadata

import pytest

from agent_core.guards.detector import build_detector, check_detector, installed_version, top2_scores
from agent_core.guards.models import GuardsConfigError

LANGS = ("en", "es", "pt")
VERSION = importlib.metadata.version("lingua-language-detector")
DETECTOR = f"lingua@{VERSION}"


def test_installed_version_matches_pin() -> None:
    assert installed_version() == VERSION


def test_check_detector_ok() -> None:
    check_detector(DETECTOR, LANGS)


def test_version_mismatch_is_startup_error() -> None:
    with pytest.raises(GuardsConfigError):
        check_detector("lingua@0.0.1", LANGS)


def test_other_detector_family_is_startup_error() -> None:
    with pytest.raises(GuardsConfigError):
        check_detector(f"fasttext@{VERSION}", LANGS)


def test_unknown_language_code_is_startup_error() -> None:
    with pytest.raises(GuardsConfigError):
        check_detector(DETECTOR, ("es", "zz"))


def test_needs_at_least_two_languages() -> None:
    with pytest.raises(GuardsConfigError):
        check_detector(DETECTOR, ("es",))


def test_detector_is_built_once_per_config() -> None:
    assert build_detector(DETECTOR, LANGS) is build_detector(DETECTOR, LANGS)


def test_top2_orders_and_restricts_candidates() -> None:
    text = "Necesito revisar el saldo de mi cuenta de ahorros por favor"
    scores = top2_scores(text, DETECTOR, LANGS)
    assert scores[0][0] == "es"
    assert len(scores) == 2 and {lang for lang, _ in scores} <= set(LANGS)
    assert scores[0][1] >= scores[1][1]
    assert scores == top2_scores(text, DETECTOR, LANGS)
