import pytest
from pydantic import BaseModel, ValidationError

from agent_core.domain.base import Model
from agent_core.domain.errors import InvalidRuntimeRef
from agent_core.domain.refs import (
    AgentSelector,
    EntityKind,
    EntityRef,
    RefSpec,
    iter_refspecs,
    require_exact_refs,
)


# T-M0-01
@pytest.mark.parametrize("text", ["tool@^1", "tool@1", "tool", "tool@~1.2", "tool@1.2"])
def test_entity_ref_parse_rejects_non_exact(text: str) -> None:
    with pytest.raises(InvalidRuntimeRef):
        EntityRef.parse(text)


def test_entity_ref_parse_exact() -> None:
    ref = EntityRef.parse("tool@1.2.0")
    assert (ref.id, ref.version) == ("tool", "1.2.0")
    assert str(ref) == "tool@1.2.0"


@pytest.mark.parametrize("text", ["tool@1.2.0\n", "tool\n@1.2.0", "tool@01.2.0", "Tool@1.2.0", "@1.0.0"])
def test_entity_ref_parse_rejects_malformed(text: str) -> None:
    with pytest.raises(InvalidRuntimeRef):
        EntityRef.parse(text)


def test_refspec_require_exact() -> None:
    with pytest.raises(InvalidRuntimeRef):
        RefSpec.parse("tool@^1").require_exact()
    with pytest.raises(InvalidRuntimeRef):
        RefSpec.parse("t/pedir_cargo").require_exact()
    assert RefSpec.parse("tool@1.2.0").require_exact() == EntityRef(id="tool", version="1.2.0")


def test_entity_ref_field_accepts_string_but_not_ranges() -> None:
    class Holder(Model):
        ref: EntityRef

    assert Holder.model_validate({"ref": "flow-a@2.0.1"}).ref == EntityRef(id="flow-a", version="2.0.1")
    with pytest.raises(ValidationError):
        Holder.model_validate({"ref": "flow-a@^2"})


# T-M0-11
@pytest.mark.parametrize(
    ("text", "ident", "spec", "exact"),
    [
        ("tool", "tool", None, False),
        ("tool@^1", "tool", "^1", False),
        ("tool@~1.2", "tool", "~1.2", False),
        ("tool@1", "tool", "1", False),
        ("tool@1.2.0", "tool", "1.2.0", True),
        ("t/pedir_cargo", "t/pedir_cargo", None, False),
        ("buscar_transacciones@1", "buscar_transacciones", "1", False),
    ],
)
def test_refspec_parse_accepts(text: str, ident: str, spec: str | None, exact: bool) -> None:
    ref = RefSpec.parse(text)
    assert (ref.id, ref.spec, ref.is_exact) == (ident, spec, exact)
    assert str(ref) == text


@pytest.mark.parametrize(
    "text",
    ["Tool@1", "mi tool", "tool@", "tool@latest", "@1.0.0", "-tool", "tool\n", "tool@1.2.0\n", "tool@٣"],
)
def test_refspec_parse_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        RefSpec.parse(text)


def test_agent_selector() -> None:
    assert AgentSelector.parse("atencion") == AgentSelector(id="atencion", alias="prod", version=None)
    canary = AgentSelector(id="atencion", alias="canary", version=None)
    assert AgentSelector.parse("atencion@canary") == canary
    assert AgentSelector.parse("atencion@1.2.0") == AgentSelector(id="atencion", alias=None, version="1.2.0")
    with pytest.raises(ValueError):
        AgentSelector.parse("atencion@^1")


@pytest.mark.parametrize(
    "text", ["atencion@", "atencion@canary\n", "atencion\n", "Atencion", "atencion@Canary"]
)
def test_agent_selector_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        AgentSelector.parse(text)


def test_agent_selector_dict_validates_alias_and_exclusivity() -> None:
    assert AgentSelector.model_validate({"id": "a"}).alias == "prod"
    with pytest.raises(ValidationError):
        AgentSelector.model_validate({"id": "a", "alias": "Bad Alias"})
    with pytest.raises(ValidationError):
        AgentSelector.model_validate({"id": "a", "alias": "prod", "version": "1.0.0"})


def test_entity_kind_matches_spec() -> None:
    assert {k.value for k in EntityKind} == {
        "agent", "flow", "decision_model", "policy", "template", "prompt", "tool",
        "language_detection", "injection_ruleset", "model_profile", "knowledge_snapshot",
    }  # fmt: skip


def test_require_exact_refs_walks_nested_models() -> None:
    class Inner(Model):
        refs: list[RefSpec]

    class Outer(Model):
        inner: Inner
        by_name: dict[str, RefSpec]

    ok = Outer.model_validate({"inner": {"refs": ["a@1.0.0"]}, "by_name": {"x": "b@2.0.0"}})
    require_exact_refs(ok)
    assert [str(r) for r in iter_refspecs(ok)] == ["a@1.0.0", "b@2.0.0"]

    ranged = Outer.model_validate({"inner": {"refs": ["a@1.0.0"]}, "by_name": {"x": "b@^2"}})
    with pytest.raises(InvalidRuntimeRef):
        require_exact_refs(ranged)
    assert isinstance(ranged, BaseModel)


def test_iter_refspecs_survives_cycles_and_deep_nesting() -> None:
    cyclic: list[object] = [RefSpec.parse("a@1.0.0")]
    cyclic.append(cyclic)
    assert [str(r) for r in iter_refspecs(cyclic)] == ["a@1.0.0"]

    deep: object = RefSpec.parse("z@1.0.0")
    for _ in range(50_000):
        deep = [deep]
    assert [str(r) for r in iter_refspecs(deep)] == ["z@1.0.0"]


def test_refspec_pattern_is_in_the_json_schema_and_behaviour_is_unchanged() -> None:
    schema = RefSpec.model_json_schema()
    spec = schema["properties"]["spec"]
    assert any("pattern" in option for option in spec["anyOf"])
    for good in ("1.2.3", "^1", "~1.2", "1", "1.2"):
        assert RefSpec.parse(f"x@{good}").spec == good
    for bad in ("1.2.3.4", "01.2.3", "latest", "^", "1.2.3\n"):
        with pytest.raises(ValueError):
            RefSpec(id="x", spec=bad)
