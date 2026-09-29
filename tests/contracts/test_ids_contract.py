"""Contrato de `IdSource`: mismas aserciones contra SystemIds y FakeIds (spec M0 T-M0-C-*)."""

import base64
import re

import pytest

from agent_core.adapters.system_ids import SystemIds
from agent_core.ports import IdKind, IdSource
from testing.fakes.ids import FakeIds

_URLSAFE_NO_PADDING = re.compile(r"[A-Za-z0-9_-]+")


def check_ids_unique_per_kind(ids: IdSource) -> None:
    for kind in IdKind:
        generated = [ids.new_id(kind) for _ in range(200)]
        assert all(isinstance(g, str) and g for g in generated)
        assert len(set(generated)) == 200


def check_secret_token(ids: IdSource) -> None:
    token = ids.secret_token()
    assert _URLSAFE_NO_PADDING.fullmatch(token), "base64 url-safe sin relleno"
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert len(raw) >= 16
    tokens = {ids.secret_token() for _ in range(100)}
    assert len(tokens) == 100
    assert token not in tokens


@pytest.fixture(params=["fake", "system"])
def ids(request: pytest.FixtureRequest) -> IdSource:
    return FakeIds() if request.param == "fake" else SystemIds()


def test_ids_unique_per_kind(ids: IdSource) -> None:
    check_ids_unique_per_kind(ids)


def test_secret_token_has_128_bits(ids: IdSource) -> None:
    check_secret_token(ids)


def test_repr_does_not_leak_tokens(ids: IdSource) -> None:
    token = ids.secret_token()
    assert token not in repr(ids)
    assert token not in str(ids)


# --- sanidad negativa: el contrato debe poder fallar ---


class _ConstantIds:
    def new_id(self, kind: IdKind) -> str:
        return "siempre-igual"

    def secret_token(self) -> str:
        return "AAAAAAAAAAAAAAAAAAAAAA"


class _ShortToken(_ConstantIds):
    def secret_token(self) -> str:
        return base64.urlsafe_b64encode(b"corto").rstrip(b"=").decode()


class _PaddedToken(_ConstantIds):
    def secret_token(self) -> str:
        return base64.urlsafe_b64encode(b"x" * 17).decode()


def test_contract_detects_duplicate_ids() -> None:
    with pytest.raises(AssertionError):
        check_ids_unique_per_kind(_ConstantIds())


def test_contract_detects_repeated_token() -> None:
    with pytest.raises(AssertionError):
        check_secret_token(_ConstantIds())


def test_contract_detects_short_or_padded_token() -> None:
    with pytest.raises(AssertionError):
        check_secret_token(_ShortToken())
    with pytest.raises(AssertionError):
        check_secret_token(_PaddedToken())


# --- propiedades propias de FakeIds ---


def test_fake_ids_reproducible_and_seedable() -> None:
    a, b = FakeIds(), FakeIds()
    assert [a.new_id(IdKind.fact) for _ in range(3)] == [b.new_id(IdKind.fact) for _ in range(3)]
    assert a.secret_token() == b.secret_token()
    seeded = FakeIds(seed={IdKind.action: ["action-grabado-1"]})
    assert seeded.new_id(IdKind.action) == "action-grabado-1"
    assert seeded.new_id(IdKind.action) == "action-0001"
    assert FakeIds().new_id(IdKind.call) == "call-0001"


def test_fake_ids_sequences_are_independent_per_kind() -> None:
    fake = FakeIds()
    assert fake.new_id(IdKind.action) == "action-0001"
    assert fake.new_id(IdKind.fact) == "fact-0001"
    assert fake.new_id(IdKind.action) == "action-0002"


def test_fake_ids_token_seed_changes_tokens() -> None:
    assert FakeIds(token_seed="a").secret_token() != FakeIds(token_seed="b").secret_token()


def test_fake_ids_never_repeat_a_seeded_value() -> None:
    fake = FakeIds(seed={IdKind.action: ["action-0001"]})
    issued = [fake.new_id(IdKind.action) for _ in range(3)]
    assert issued == ["action-0001", "action-0002", "action-0003"]


def test_fake_ids_reject_repeated_seed_values() -> None:
    with pytest.raises(ValueError):
        FakeIds(seed={IdKind.action: ["x", "x"]})


def test_fake_ids_seed_is_copied() -> None:
    values = ["a-1", "a-2"]
    fake = FakeIds(seed={IdKind.action: values})
    values.clear()
    assert fake.new_id(IdKind.action) == "a-1"
