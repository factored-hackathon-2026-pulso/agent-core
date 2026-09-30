from decimal import Decimal

import pytest
from pydantic import ValidationError

from agent_core.registry.models import EntityDraft, VersionDocs, VersionRef
from agent_core.registry.suite import EvalSuite
from tests.registry.helpers import docs, suite_content


def test_version_ref_is_hashable_and_printable() -> None:
    ref = VersionRef(kind="prompt", id="p/x", version="1.0.0")
    assert str(ref) == "prompt:p/x@1.0.0"
    assert {ref: 1}[VersionRef(kind="prompt", id="p/x", version="1.0.0")] == 1


def test_draft_exposes_id_and_version() -> None:
    d = EntityDraft(kind="template", content={"id": "t/x", "version": "1.2.0"}, docs=docs())
    assert (d.id, d.version) == ("t/x", "1.2.0")


def test_draft_without_id_or_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EntityDraft(kind="template", content={"id": "t/x"}, docs=docs())


def test_docs_require_description() -> None:
    with pytest.raises(ValidationError):
        VersionDocs(description="", rationale="r", changelog="c")


def test_suite_parses_with_decimal_thresholds() -> None:
    suite = EvalSuite.model_validate(suite_content())
    assert suite.noise_margin == Decimal("0.05") and suite.floor == Decimal("0.5")
    assert suite.scenarios[0].steps[0].op == "start"


@pytest.mark.parametrize("over", [
    {"scenarios": []},
    {"repetitions": 0},
    {"noise_margin": "1.5"},
    {"scenarios": [suite_content()["scenarios"][0], suite_content()["scenarios"][0]]},  # ids repetidos
])
def test_suite_rejects_bad_shapes(over: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvalSuite.model_validate(suite_content(**over))


def test_scenario_must_start_with_start_step() -> None:
    bad = suite_content()
    bad["scenarios"][0]["steps"] = [{"op": "turn", "text": "hola"}]
    with pytest.raises(ValidationError):
        EvalSuite.model_validate(bad)
