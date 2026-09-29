from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain.identity import AuthLevel, OnBehalfOf, Principal, PrincipalKey, PrincipalType

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _principal(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "customer",
        "id": "cust-001",
        "auth": {"level": "session", "at": NOW},
        "exp": NOW + timedelta(hours=1),
    }
    return base | over


# T-M0-08
def test_session_customer_valid_and_key() -> None:
    principal = Principal.model_validate(_principal())
    assert principal.key == PrincipalKey(type=PrincipalType.customer, id="cust-001")


def test_anonymous_customer_valid() -> None:
    Principal.model_validate(_principal(id=None, auth={"level": "anonymous", "at": NOW}))


def test_anonymous_with_id_rejected() -> None:
    with pytest.raises(ValidationError):
        Principal.model_validate(_principal(auth={"level": "anonymous", "at": NOW}))


def test_session_without_id_rejected() -> None:
    with pytest.raises(ValidationError):
        Principal.model_validate(_principal(id=None))


def test_anonymous_advisor_rejected() -> None:
    with pytest.raises(ValidationError):
        Principal.model_validate(_principal(type="advisor", id=None, auth={"level": "anonymous", "at": NOW}))


def test_empty_id_rejected_fail_closed() -> None:
    # "" no es None: sin min_length un principal con sesión y id vacío pasaría como identificado.
    with pytest.raises(ValidationError):
        Principal.model_validate(_principal(id=""))
    with pytest.raises(ValidationError):
        PrincipalKey.model_validate({"type": "advisor", "id": ""})


def test_principal_rejects_extra_fields_such_as_credentials() -> None:
    with pytest.raises(ValidationError):
        Principal.model_validate(_principal(token="secreto"))


def test_principal_is_immutable() -> None:
    principal = Principal.model_validate(_principal())
    with pytest.raises(ValidationError):
        principal.id = "otro"


def test_attrs_only_accept_strings() -> None:
    with pytest.raises(ValidationError):
        Principal.model_validate(_principal(attrs={"segmento": {"nested": 1}}))
    with pytest.raises(ValidationError):
        Principal.model_validate(_principal(attrs={"n": 1}))


def test_auth_level_ordering_is_by_rank_not_alphabet() -> None:
    assert AuthLevel.session >= AuthLevel.anonymous
    assert AuthLevel.step_up > AuthLevel.session
    assert AuthLevel.anonymous < AuthLevel.step_up
    assert not (AuthLevel.session < AuthLevel.anonymous)
    assert AuthLevel.step_up.rank == 2
    assert sorted([AuthLevel.step_up, AuthLevel.anonymous, AuthLevel.session]) == [
        AuthLevel.anonymous,
        AuthLevel.session,
        AuthLevel.step_up,
    ]


def test_auth_level_mixed_type_compare_fails_closed() -> None:
    # Sin TypeError explícito, Python cae al `str.__lt__` reflejado y compara alfabéticamente.
    with pytest.raises(TypeError):
        _ = AuthLevel.session >= "anonymous"
    with pytest.raises(TypeError):
        _ = "anonymous" <= AuthLevel.session
    with pytest.raises(TypeError):
        _ = AuthLevel.anonymous < 1


def _obo(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "subject": {"kind": "customer", "ref": "cust-001"},
        "grant_ref": "grant-9",
        "grantee": {"type": "advisor", "id": "adv-7"},
        "scopes": ["read"],
        "exp": NOW + timedelta(hours=1),
    }
    return base | over


def test_on_behalf_of_requires_advisor_grantee() -> None:
    OnBehalfOf.model_validate(_obo())
    with pytest.raises(ValidationError):
        OnBehalfOf.model_validate(_obo(grantee={"type": "customer", "id": "cust-001"}))
    with pytest.raises(ValidationError):
        OnBehalfOf.model_validate(_obo(grantee={"type": "advisor", "id": ""}))
    with pytest.raises(ValidationError):
        OnBehalfOf.model_validate(_obo(grantee={"type": "advisor", "id": None}))
    with pytest.raises(ValidationError):
        OnBehalfOf.model_validate({k: v for k, v in _obo().items() if k != "grantee"})


def test_on_behalf_of_requires_grant_ref_and_subject() -> None:
    with pytest.raises(ValidationError):
        OnBehalfOf.model_validate(_obo(grant_ref=""))
    with pytest.raises(ValidationError):
        OnBehalfOf.model_validate(_obo(subject={"kind": "", "ref": "x"}))
