"""Detector de PII en texto libre (M7 §3.2)."""

from itertools import pairwise

from agent_core.views.detector import detect, digit_runs


def _found(text: str) -> list[tuple[str, str]]:
    return [(hit.tag, hit.value) for hit in detect(text)]


def test_email() -> None:
    assert _found("escribe a ana.perez@example.test hoy") == [("email", "ana.perez@example.test")]


def test_document_plain_with_dots_and_glued_to_letters() -> None:
    assert _found("mi cédula es 1023456789.") == [("doc", "1023456789")]
    assert _found("CC 1.023.456.789") == [("doc", "1023456789")]
    assert _found("CC1023456789") == [("doc", "1023456789")]


def test_phone() -> None:
    assert _found("llama al +57 300 123 4567") == [("tel", "+573001234567")]
    assert _found("o al 300 123 4567") == [("tel", "3001234567")]
    assert _found("o al 300-123-4567") == [("tel", "3001234567")]


def test_account() -> None:
    assert _found("cuenta 0012-3456-7890-1234") == [("prod", "0012345678901234")]


def test_long_digit_run_is_detected() -> None:
    assert _found("9" * 30) == [("prod", "9" * 30)]


def test_short_numbers_and_iso_dates_are_ignored() -> None:
    assert _found("pagué 500 el 12/09 y otra vez el 2026-09-12") == []


def test_amount_like_numbers_are_conservatively_detected() -> None:
    assert _found("me cobraron $ 1.500.000") == [("doc", "1500000")]


def test_digits_inside_email_are_not_double_counted() -> None:
    assert _found("user1234567@example.test") == [("email", "user1234567@example.test")]


def test_hits_are_sorted_and_disjoint() -> None:
    hits = detect("doc 1023456789, correo ana@example.test, tel +57 300 123 4567")
    assert [hit.tag for hit in hits] == ["doc", "email", "tel"]
    assert all(a.end <= b.start for a, b in pairwise(hits))


def test_hit_repr_has_no_value() -> None:
    assert "1023456789" not in repr(detect("1023456789")[0])


def test_digit_runs() -> None:
    assert digit_runs("pagué 1.500 el +57 300 123 4567") == {"1500", "573001234567"}
