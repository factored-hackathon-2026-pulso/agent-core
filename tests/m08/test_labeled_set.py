"""Falsos rechazos y fugas de `validate` sobre el conjunto etiquetado sintético ES/PT (spec §8, §10)."""

from collections import Counter

import pytest

from tests.m08.labeled_set import LABELED, P2_PROBE, LabeledCase, run_case


def _checks(case: LabeledCase) -> list[str]:
    return list(dict.fromkeys(f.check for f in run_case(case).failures))


def test_set_size_languages_and_hygiene() -> None:
    assert len(LABELED) >= 30 and {c.locale for c in LABELED} == {"es", "pt"}
    assert all(c.synthetic for c in LABELED + P2_PROBE)
    assert len({c.id for c in LABELED + P2_PROBE}) == len(LABELED + P2_PROBE)
    assert sum(c.expected_ok and c.locale == "es" for c in LABELED) >= 12
    assert sum(c.expected_ok and c.locale == "pt" for c in LABELED) >= 12
    assert sum(not c.expected_ok for c in LABELED) >= 6
    for case in LABELED + P2_PROBE:  # solo datos sintéticos: sin correos ni dominios reales
        assert "@" not in case.text or "example.test" in case.text


@pytest.mark.parametrize("case", [c for c in LABELED if c.expected_ok], ids=lambda c: c.id)
def test_correct_responses_are_not_rejected(case: LabeledCase) -> None:
    assert run_case(case).failures == []


@pytest.mark.parametrize("case", [c for c in LABELED if not c.expected_ok], ids=lambda c: c.id)
def test_incorrect_responses_fail_with_exactly_their_checks(case: LabeledCase) -> None:
    assert _checks(case) == case.expected_checks


def test_false_reject_rate_report_by_language_and_check(capsys: pytest.CaptureFixture[str]) -> None:
    correct = [c for c in LABELED if c.expected_ok]
    rejected = [c for c in correct if not run_case(c).ok]
    by_language = {
        loc: sum(c.locale == loc for c in rejected) / sum(c.locale == loc for c in correct)
        for loc in ("es", "pt")
    }
    by_check = Counter(f.check for c in correct for f in run_case(c).failures)
    print(
        f"\nfalsos rechazos (conjunto principal): {len(rejected)}/{len(correct)} por idioma {by_language} "
        f"por comprobación {dict(by_check)}"
    )
    assert rejected == [] and by_language == {"es": 0.0, "pt": 0.0}


def test_p2_probe_measures_false_rejection_of_non_business_numbers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """P2 (decidido): estos números cuentan igual. Se mide cuántas respuestas correctas se rechazan; no se
    relaja ninguna regla. Línea base actual: la regla vigente rechaza todos los casos del sondeo."""
    results = {c.id: run_case(c) for c in P2_PROBE}
    rejected = [cid for cid, r in results.items() if not r.ok]
    by_language = {
        loc: sum(not results[c.id].ok for c in P2_PROBE if c.locale == loc)
        / sum(c.locale == loc for c in P2_PROBE)
        for loc in ("es", "pt")
    }
    print(
        f"\nsondeo P2: falso rechazo {len(rejected)}/{len(P2_PROBE)} por idioma {by_language} "
        f"solo por la comprobación 'numbers'"
    )
    assert all({f.check for f in r.failures} <= {"numbers"} for r in results.values())
    assert len(rejected) == len(P2_PROBE)
