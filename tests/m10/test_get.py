"""`get`: T-M10-05. Renderizado por lector con M7; sin delegación, 403."""

from decimal import Decimal

import pytest

from agent_core.domain import EngineError, EscalationRequest, ProblemCode
from testing.builders import advisor_with_delegation, principal
from tests.m10.helpers import DOC, HandoffAuthz, escalate_and_commit, make_state, make_world, seed_run

REQUEST = EscalationRequest(reason_code="customer_request", target_queue="disputas", priority="normal")


def _world(grants: set[tuple[str, str]]):  # type: ignore[no-untyped-def]
    world = make_world(authz=HandoffAuthz(grants))
    state = seed_run(world, make_state())
    escalate_and_commit(world, state, REQUEST)
    return world


def test_t_m10_05_advisor_with_delegation_sees_the_permitted_fields() -> None:
    """T-M10-05: el asesor con delegación ve los campos permitidos; el resto, enmascarado."""
    world = _world({("adv-7", "document_number")})
    advisor, obo = advisor_with_delegation()
    out = world.service.get("handoff-0001", advisor, obo)
    cliente = out["verified_facts"][0]["value"]  # type: ignore[index]
    assert cliente == {"document_number": DOC, "first_name": "***"}
    assert out["verified_facts"][1]["value"] == {"amount": Decimal("120.50"), "currency": "USD"}  # type: ignore[index]
    assert out["claimed_not_verified"][0]["value"] == "***"  # type: ignore[index]  # `documento` sin clasificar
    assert out["subject"] == {"kind": "customer", "ref": "cust-001"}  # el asesor delegado conoce el subject
    assert out["resolution"] is None and out["handoff_ref"] == "handoff-0001"
    assert out["transcript_ref"] == "/v1/runs/run-0001/transcript"


def test_t_m10_05_without_delegation_it_is_forbidden() -> None:
    """T-M10-05: sin delegación, `403`."""
    world = _world({("adv-7", "document_number")})
    advisor, _ = advisor_with_delegation()
    with pytest.raises(EngineError) as denied:
        world.service.get("handoff-0001", advisor, None)
    assert denied.value.code is ProblemCode.subject_forbidden and denied.value.status == 403
    with pytest.raises(EngineError) as customer:
        world.service.get("handoff-0001", principal(), None)  # un customer tampoco lee handoffs
    assert customer.value.status == 403


def test_delegation_over_another_subject_is_forbidden() -> None:
    world = _world(set())
    advisor, obo = advisor_with_delegation()
    other = obo.model_copy(update={"subject": {"kind": "customer", "ref": "cust-999"}})
    with pytest.raises(EngineError) as denied:
        world.service.get("handoff-0001", advisor, other)
    assert denied.value.status == 403


def test_service_principal_with_scope_can_read() -> None:
    world = _world({("svc-1", "document_number")})
    service = principal(type="service", id="svc-1", scopes=["handoff:read"], attrs={})
    out = world.service.get("handoff-0001", service)
    assert out["verified_facts"][0]["value"]["document_number"] == DOC  # type: ignore[index]
    with pytest.raises(EngineError):
        world.service.get("handoff-0001", principal(type="service", id="svc-2", scopes=[], attrs={}))


def test_unknown_handoff_is_not_found() -> None:
    world = _world(set())
    advisor, obo = advisor_with_delegation()
    with pytest.raises(EngineError) as missing:
        world.service.get("handoff-9999", advisor, obo)
    assert missing.value.code is ProblemCode.not_found and missing.value.status == 404


def test_get_does_not_change_what_is_persisted() -> None:
    world = _world({("adv-7", "document_number")})
    advisor, obo = advisor_with_delegation()
    before = world.store.handoffs["handoff-0001"]
    world.service.get("handoff-0001", advisor, obo)
    assert world.store.handoffs["handoff-0001"] == before
    assert world.store.runs["run-0001"].token_map is None  # el vault de `get` es descartable
