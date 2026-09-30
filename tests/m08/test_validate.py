import typing

from agent_core.response.checks import CHECKS, Check
from agent_core.response.types import CheckId, Draft, Failure, ValidationResult
from agent_core.response.validate import validate
from tests.m08.helpers import DOT_COMMA, make_ctx, make_fact

PT = "Gostaria de consultar o saldo da minha conta e revisar as últimas transações. Total 999,00 ⟦doc:9⟧"
ES = "Quiero consultar el saldo de mi cuenta y revisar los últimos movimientos por 100,00"
FACTS = {"f1": make_fact("f1", {"monto": "100.00"}), "f2": make_fact("f2", {"otro": 5})}


def test_checks_list_has_the_seven_ids_in_order() -> None:
    assert [cid for cid, _ in CHECKS] == [
        "format", "citations", "numbers", "tokens_pii", "language", "page_citations", "page_audience"]
    assert set(typing.get_args(CheckId)) == {cid for cid, _ in CHECKS}


def test_all_failures_are_reported_in_check_order() -> None:
    ctx = make_ctx(FACTS, {"f1"}, number_format=DOT_COMMA)
    result = validate(Draft(text=PT, citations=["f2"]), ctx)
    assert not result.ok
    assert [f.check for f in result.failures] == ["citations", "numbers", "tokens_pii", "language"]


def test_valid_draft_is_ok() -> None:
    result = validate(Draft(text=ES, citations=["f1"]), make_ctx(FACTS, {"f1"}, number_format=DOT_COMMA))
    assert result == ValidationResult(ok=True, failures=[])


def test_validate_is_pure_and_deterministic() -> None:
    ctx = make_ctx(FACTS, {"f1"}, number_format=DOT_COMMA)
    draft = Draft(text=PT, citations=["f2"])
    before = (draft.model_dump(), dict(ctx.facts_model_view), set(ctx.allowed))
    assert validate(draft, ctx) == validate(draft, ctx)
    assert (draft.model_dump(), dict(ctx.facts_model_view), set(ctx.allowed)) == before


def test_a_new_check_runs_in_its_position_without_touching_the_global_list() -> None:
    def fake(draft: Draft, ctx: object) -> list[Failure]:
        return [Failure(check="format", detail="falsa")]

    custom: tuple[tuple[CheckId, Check], ...] = (*CHECKS[:2], ("format", fake), *CHECKS[2:])  # type: ignore[arg-type]
    ctx = make_ctx(FACTS, {"f1"}, number_format=DOT_COMMA)
    result = validate(Draft(text=ES, citations=["f1"]), ctx, checks=custom)
    assert [f.detail for f in result.failures] == ["falsa"] and len(CHECKS) == 7
