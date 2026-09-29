from agent_core.response.checks import check_format
from agent_core.response.types import Draft
from tests.m08.helpers import make_ctx


def test_empty_or_blank_text_fails_format() -> None:
    for text in ("", "   ", "\n\t"):
        failures = check_format(Draft(text=text, citations=[]), make_ctx())
        assert [f.check for f in failures] == ["format"]


def test_valid_text_passes() -> None:
    assert check_format(Draft(text="Hola, tu caso sigue en curso.", citations=["f1"]), make_ctx()) == []


def test_duplicate_citations_are_allowed() -> None:
    assert check_format(Draft(text="ok", citations=["f1", "f1"]), make_ctx()) == []


def test_does_not_mutate_the_draft() -> None:
    draft = Draft(text="ok", citations=["f1"])
    before = draft.model_dump()
    check_format(draft, make_ctx())
    assert draft.model_dump() == before
