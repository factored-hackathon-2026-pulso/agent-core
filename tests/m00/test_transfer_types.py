"""Transfer types (ADR 0021, spec §3.1): routing card, input contract, directory and eligibility."""

from decimal import Decimal

import pytest

from agent_core.domain import (
    AcceptedSlot,
    Agent,
    DirectoryEntry,
    DirectorySnapshot,
    SubjectRef,
    TransferContract,
    directory_hash,
    packet_problem,
    transfer_ineligibility,
)
from testing.builders import principal
from tests.m04.harness import agent_data

CONTRACT = TransferContract(slots={"problem": AcceptedSlot(type="string", required=True),
                                   "amount": AcceptedSlot(type="decimal")})
CUSTOMER_SUBJECT = SubjectRef(kind="customer", ref="cust-001")


def _specialist(**over: object) -> Agent:
    data = agent_data("disputas", routing={"directory": "customer-care", "summary": "Disputes",
                                           "examples": ["no reconozco un cargo"]},
                      accepts=CONTRACT.model_dump(mode="json"), understand="understand@1.0.0")
    return Agent.model_validate(data | over)


def test_directory_hash_is_order_independent_and_changes_with_releases() -> None:
    a = directory_hash([("disputas", "rel-1"), ("saldos", "rel-2")])
    assert a == directory_hash([("saldos", "rel-2"), ("disputas", "rel-1")])
    assert a != directory_hash([("disputas", "rel-9"), ("saldos", "rel-2")])
    assert len(a) == 64


def test_snapshot_choices_are_the_agent_ids_in_order() -> None:
    entry = DirectoryEntry(agent_id="disputas", release_id="rel-1", summary="s", examples=[],
                           accepts=CONTRACT, supported_locales=["es"])
    snap = DirectorySnapshot(directory="customer-care", hash="a" * 64, entries=[entry])
    assert snap.choices == ["disputas"]


@pytest.mark.parametrize(("slots", "problem"), [
    ({"problem": "x"}, None),
    ({"problem": "x", "amount": Decimal("12.50")}, None),
    ({}, "missing_required_slot"),
    ({"problem": "x", "other": "y"}, "slot_not_accepted"),
    ({"problem": 5}, "slot_type_mismatch"),
    ({"problem": "x", "amount": 1.5}, "slot_type_mismatch"),  # float is never a decimal
])
def test_packet_problem(slots: dict[str, object], problem: str | None) -> None:
    assert packet_problem(CONTRACT, slots) == problem  # type: ignore[arg-type]


def test_eligibility_reasons_carry_no_data() -> None:
    agent = _specialist()
    assert transfer_ineligibility(agent, principal(), CUSTOMER_SUBJECT, "es") is None
    assert transfer_ineligibility(agent, principal(type="advisor", id="adv-1"), CUSTOMER_SUBJECT,
                                  "es") == "principal_type"
    assert transfer_ineligibility(agent, principal(), SubjectRef(kind="account", ref="a-1"),
                                  "es") == "subject_kind"
    assert transfer_ineligibility(agent, principal(), CUSTOMER_SUBJECT, "en") == "locale"
    step_up = _specialist(min_auth_level="step_up")
    assert transfer_ineligibility(step_up, principal(), CUSTOMER_SUBJECT, "es") == "auth_level"
    no_contract = _specialist(accepts=None)
    assert transfer_ineligibility(no_contract, principal(), CUSTOMER_SUBJECT, "es") == "no_contract"
    task = _specialist(mode="task", subject_kinds=[])
    assert transfer_ineligibility(task, principal(), None, "es") == "mode"
