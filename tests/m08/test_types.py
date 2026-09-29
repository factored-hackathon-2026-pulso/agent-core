from agent_core.response.types import Draft, Failure, ValidationResult, parse_draft
from tests.m08.helpers import make_ctx, make_fact


def test_parse_draft_accepts_text_and_citations() -> None:
    assert parse_draft({"text": "hola", "citations": ["f1"]}) == Draft(text="hola", citations=["f1"])


def test_parse_draft_rejects_bad_shapes_as_format_failure() -> None:
    for bad in (None, "texto", {"text": "x"}, {"text": 1, "citations": []}, {"text": "x", "citations": "f1"},
                {"text": "x", "citations": [1]}, {"text": "x", "citations": [], "extra": 1}):
        out = parse_draft(bad)  # type: ignore[arg-type]
        assert isinstance(out, Failure) and out.check == "format"


def test_failure_detail_never_echoes_the_payload() -> None:
    out = parse_draft({"text": 1234567890, "citations": []})
    assert isinstance(out, Failure) and "1234567890" not in out.detail


def test_result_ok_is_derived_from_failures() -> None:
    assert ValidationResult(ok=True, failures=[]).ok
    assert not ValidationResult(ok=False, failures=[Failure(check="numbers", detail="x")]).ok


def test_context_repr_hides_facts_and_vault() -> None:
    ctx = make_ctx({"f1": make_fact("f1", {"doc": "1023456789"})})
    assert "1023456789" not in repr(ctx) and "vault" not in repr(ctx)
