import pytest

from agent_core.domain import Prompt
from agent_core.registry.entities import (
    SUITE_KIND,
    content_hash,
    decode_entity,
    encode_entity,
    entity_kind,
    version_ref,
)
from agent_core.registry.errors import IntegrityError, RegistryError
from agent_core.registry.suite import EvalSuite
from tests.registry.helpers import demo_pinned, suite_content


def test_every_demo_entity_round_trips_with_same_hash() -> None:
    for entity in demo_pinned().entities:
        data = encode_entity(entity)
        again = decode_entity(entity_kind(entity), data)
        assert again == entity
        assert content_hash(again) == content_hash(entity)


def test_suite_is_an_entity_of_its_own_kind() -> None:
    suite = EvalSuite.model_validate(suite_content())
    assert entity_kind(suite) == SUITE_KIND
    assert str(version_ref(suite)) == "eval_suite:disputas-suite@1.0.0"
    assert decode_entity(SUITE_KIND, encode_entity(suite)) == suite


def test_decode_unknown_kind_fails() -> None:
    with pytest.raises(RegistryError):
        decode_entity("nope", b"{}")


def test_decode_garbage_is_integrity_error() -> None:
    with pytest.raises(IntegrityError):
        decode_entity("prompt", b"{not json")


def test_prompt_kind_name() -> None:
    prompt = next(e for e in demo_pinned().entities if isinstance(e, Prompt))
    assert entity_kind(prompt) == "prompt"
