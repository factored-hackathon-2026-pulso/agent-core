"""`PolicyAuthz` configuration: bound-parameter keys and field grants from a file (synthetic data)."""

import json
from pathlib import Path

import pytest

from agent_core.adapters.policy_authz import (
    BIND_KEYS_ENV,
    FIELD_GRANTS_ENV,
    PolicyAuthz,
    from_env,
    load_grants,
)
from agent_core.domain import SchemaError
from testing.builders import advisor_with_delegation, principal
from testing.fakes.authz import TableAuthz


def test_the_default_binds_only_subject_ref_and_the_test_double_is_the_same_class() -> None:
    assert issubclass(TableAuthz, PolicyAuthz)
    assert PolicyAuthz().bind_params(principal(), None, None) == {"subject_ref": "cust-001"}


def test_bind_keys_name_every_parameter_that_carries_the_subject() -> None:
    authz = PolicyAuthz(bind_keys=("subject_ref", "customer_id"))
    who, grant = advisor_with_delegation()

    both = {"subject_ref": "cust-001", "customer_id": "cust-001"}
    assert authz.bind_params(principal(), None, None) == both
    assert authz.bind_params(who, grant, None) == both
    no_delegation = principal(type="advisor", id="adv-7", attrs={})
    assert authz.bind_params(no_delegation, None, None) == {}


def test_no_bind_keys_is_a_configuration_error() -> None:
    with pytest.raises(ValueError, match="bind_keys"):
        PolicyAuthz(bind_keys=())


def test_grants_come_from_a_file_and_without_one_nobody_reads_fields(tmp_path: Path) -> None:
    path = tmp_path / "grants.json"
    path.write_text(json.dumps([["amount", "transcript_read"]]), encoding="utf-8")
    who, grant = advisor_with_delegation()

    configured = from_env({FIELD_GRANTS_ENV: str(path), BIND_KEYS_ENV: "subject_ref, customer_id"})
    closed = from_env({})

    assert configured.can_read_field(who, grant, "amount", "transcript_read")
    assert not configured.can_read_field(who, grant, "amount", "otra_finalidad")
    assert not closed.can_read_field(who, grant, "amount", "transcript_read")  # fails closed
    assert closed.bind_params(principal(), None, None) == {"subject_ref": "cust-001"}


@pytest.mark.parametrize("content", ["no json", "{}", '[["solo-uno"]]', '[["a", 1]]', '[["", "x"]]'])
def test_a_malformed_grants_file_stops_the_process(tmp_path: Path, content: str) -> None:
    path = tmp_path / "grants.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(SchemaError):
        load_grants(path)
    with pytest.raises(SchemaError):
        load_grants(tmp_path / "no-existe.json")


def test_empty_bind_keys_in_the_environment_are_rejected() -> None:
    with pytest.raises(SchemaError):
        from_env({BIND_KEYS_ENV: " , "})
