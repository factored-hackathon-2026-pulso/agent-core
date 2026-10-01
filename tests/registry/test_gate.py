from decimal import Decimal

from agent_core.registry.evaluation.gate import decide
from agent_core.registry.evaluation.report import SuiteMetrics
from agent_core.registry.suite import EvalSuite
from tests.registry.helpers import suite_content

SUITE = EvalSuite.model_validate(suite_content())  # margen 0.05, piso 0.5
ZERO = {"unverified_writes": 0, "unsupported_success": 0, "sensitive_leaks": 0}


def _m(primary: str, **g: int) -> SuiteMetrics:
    return SuiteMetrics(primary=Decimal(primary), guardrails={**ZERO, **g}, runs=10)


def test_guardrail_regression_fails_even_if_primary_improves() -> None:  # T-REG-08
    verdict, checks = decide(SUITE, _m("0.9", unverified_writes=1), _m("0.5"))
    assert verdict == "fail"
    assert [c.name for c in checks if not c.passed] == ["unverified_writes"]


def test_primary_within_margin_passes_outside_fails() -> None:  # T-REG-09
    assert decide(SUITE, _m("0.76"), _m("0.80"))[0] == "pass"
    assert decide(SUITE, _m("0.74"), _m("0.80"))[0] == "fail"


def test_without_base_uses_floor_and_zero_guardrails() -> None:  # T-REG-10
    assert decide(SUITE, _m("0.5"), None)[0] == "pass"
    assert decide(SUITE, _m("0.49"), None)[0] == "fail"
    assert decide(SUITE, _m("0.9", sensitive_leaks=1), None)[0] == "fail"


def test_every_check_reports_value_base_and_threshold() -> None:
    _, checks = decide(SUITE, _m("0.8"), _m("0.8"))
    primary = next(c for c in checks if c.name == "primary")
    expected = (Decimal("0.8"), Decimal("0.8"), Decimal("0.75"))
    assert (primary.value, primary.base, primary.threshold) == expected
