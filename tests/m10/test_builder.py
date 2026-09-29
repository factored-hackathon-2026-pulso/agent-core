"""Construcción del paquete: T-M10-02, T-M10-03 y la parte de T-M10-04 que no depende del servicio."""

from collections.abc import Sequence
from decimal import Decimal

from agent_core.domain import EngineEvent, EscalationRequest, RunState, dumps
from agent_core.handoff.builder import build_minimal_packet, build_packet, evidence_refs
from agent_core.handoff.packet import HandoffPacket
from agent_core.handoff.projection import Projector
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from tests.m10.helpers import DOC, make_state, make_views, rule_evaluated, tool_called

REQUEST = EscalationRequest(reason_code="customer_request", target_queue="disputas", priority="normal")


def _build(state: RunState | None = None, request: EscalationRequest = REQUEST,
           events: Sequence[EngineEvent] = ()) -> HandoffPacket:
    keys = FakeKeyProvider.default()
    views = make_views(keys=keys)
    return build_packet(state=state or make_state(), request=request, handoff_ref="handoff-0001",
                        events=list(events), projector=Projector(views, keys, FakeIds()), views=views)


def test_t_m10_02_packet_separates_verified_facts_from_claimed_slots() -> None:
    """T-M10-02: los hechos verificados y los slots `claimed` van en campos distintos."""
    packet = _build()
    assert [f.name for f in packet.verified_facts] == ["cliente", "cargo", "politica_pagina"]
    assert [s.name for s in packet.claimed_not_verified] == ["documento"]  # el slot validated no va
    assert packet.verified_facts[0].source.ref == "get_customer@1.0.0"
    assert packet.verified_facts[0].value == {"document_number": "***6789", "first_name": "***"}
    assert packet.verified_facts[1].value == {"amount": Decimal("120.50"), "currency": "USD"}
    assert packet.claimed_not_verified[0].value == "***"


def test_t_m10_03_actions_taken_reflects_verification_state() -> None:
    """T-M10-03: `actions_taken` refleja verified, uncertain y cancelled."""
    packet = _build()
    states = {a.action_id: a.state.value for a in packet.actions_taken}
    assert states == {"action-0001": "verified", "action-0002": "uncertain", "action-0003": "cancelled"}
    cancelled = next(a for a in packet.actions_taken if a.action_id == "action-0003")
    assert cancelled.cancel_reason is not None and cancelled.cancel_reason.value == "escalated"
    assert all(a.args["monto"] == Decimal("500.00") for a in packet.actions_taken)  # financial pasa
    assert all(a.args["transaction_id"] == "***" for a in packet.actions_taken)  # pii_direct tag tx


def test_packet_header_and_transcript_reference() -> None:
    packet = _build()
    assert (packet.handoff_ref, packet.run_id, packet.release) == (
        "handoff-0001", "run-0001", "rel-2026-09-28")
    assert str(packet.agent) == "atencion@1.0.0"
    assert packet.principal_type.value == "customer"
    assert packet.subject is not None and (packet.subject.kind, packet.subject.ref) == ("customer", "***")
    assert (packet.target_queue, packet.priority, packet.reason_code) == (
        "disputas", "normal", "customer_request")
    assert packet.language == "es"
    assert packet.transcript_ref == "/v1/runs/run-0001/transcript"
    assert packet.degraded_packet is False
    assert packet.request_summary.citations == ["cliente", "cargo", "politica_pagina"]
    assert "disputa-cargo" in packet.request_summary.text


def test_evidence_refs_collect_calls_policies_decisions_and_pages_without_duplicates() -> None:
    events = [tool_called("call-0001"), rule_evaluated("escalamiento-disputa-monto@1.0.0"),
              tool_called("call-0002"), tool_called("call-0001"), rule_evaluated(None),
              rule_evaluated("escalamiento-disputa-monto@1.0.0")]
    refs = evidence_refs(make_state(), events)
    assert refs == ["call:call-0001", "policy:escalamiento-disputa-monto@1.0.0", "call:call-0002",
                    "decision:decision-0001", "page:disputas/plazos@snap-1.0.0"]


def test_open_questions_with_clear_pii_are_masked() -> None:
    state = make_state(open_questions=["¿fecha exacta del cargo?", f"¿confirmas el documento {DOC}?"])
    assert _build(state).open_questions == ["¿fecha exacta del cargo?", "***"]


def test_built_packet_has_no_clear_pii() -> None:
    """Parte de T-M10-04: el paquete construido no contiene `pii_direct` en claro."""
    state = make_state()
    packet = _build(state)
    facts_full = {name: fact.value for name, fact in state.facts.items()} | {"documento": DOC}
    assert make_views().find_clear_pii(dumps(packet.model_dump(mode="python")), facts_full) == []
    assert DOC not in dumps(packet.model_dump(mode="python"))


def test_minimal_packet_is_degraded_and_carries_no_values() -> None:
    packet = build_minimal_packet(state=make_state(), request=REQUEST, handoff_ref="handoff-0001")
    assert packet.degraded_packet is True
    assert packet.reason_code == "customer_request"
    assert packet.transcript_ref == "/v1/runs/run-0001/transcript"
    assert [f.name for f in packet.verified_facts] == ["cliente", "cargo", "politica_pagina"]
    assert all(f.value is None for f in packet.verified_facts)
    assert packet.claimed_not_verified == [] and packet.actions_taken == []
    assert packet.subject is not None and packet.subject.ref == "***"
