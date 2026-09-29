"""Ayudantes de las pruebas de M10. Solo datos sintéticos."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from agent_core.domain import (
    Agent,
    EngineEvent,
    EntityRef,
    OnBehalfOf,
    Principal,
    PrincipalType,
    RuleEvaluated,
    RuleEvaluatedPayload,
    RunState,
    SubjectRef,
    ToolCalled,
    ToolCalledPayload,
)
from agent_core.ports import AuthzDecision
from agent_core.views import DEFAULT_CATALOG, FieldClassifier, FieldRule, ViewService
from testing.builders import NOW, action, run_state
from testing.fakes.clock import FakeClock
from testing.fakes.keys import FakeKeyProvider

# Catálogo sintético: lo que en producción publica la unidad 3 como FieldClassification.
CATALOG: Mapping[str, FieldRule] = {
    **DEFAULT_CATALOG,
    "amount": FieldRule(field_class="financial"),
    "currency": FieldRule(field_class="public"),
    "monto": FieldRule(field_class="financial"),
    "transaction_id": FieldRule(field_class="pii_direct", tag="tx"),
}


class HandoffAuthz:
    """Doble mínimo de `AuthzPort` para M10 (la `TableAuthz` real llega con M9).

    - `authorize_subject`: asesor con delegación a su nombre sobre ese subject; `service` con el scope
      `handoff:read`; nadie más.
    - `can_read_field`: solo para el propósito `handoff` y si el par `(lector, campo)` está concedido; el
      asesor además necesita su delegación."""

    def __init__(self, field_grants: set[tuple[str, str]] | None = None,
                 reportable: frozenset[str] = frozenset({"country"})) -> None:
        self._grants = field_grants or set()
        self._reportable = reportable

    def authorize_subject(self, principal: Principal, obo: OnBehalfOf | None,
                          subject: SubjectRef | None) -> AuthzDecision:
        if principal.type is PrincipalType.service:
            allowed = "handoff:read" in principal.scopes
        elif principal.type is PrincipalType.advisor:
            allowed = (obo is not None and obo.grantee == principal.key and subject is not None
                       and obo.subject == subject)
        else:
            allowed = False
        return AuthzDecision(allowed=allowed, reason=None if allowed else "sin_delegacion")

    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool:
        if purpose != "handoff" or reader.id is None:
            return False
        if reader.type is PrincipalType.advisor and (obo is None or obo.grantee.id != reader.id):
            return False
        return (reader.id, field) in self._grants

    def reportable_attrs(self) -> frozenset[str]:
        return self._reportable

    def authorize_agent(self, principal: Principal, agent: Agent,
                        subject: SubjectRef | None) -> AuthzDecision:
        raise NotImplementedError

    def bind_params(self, principal: Principal, obo: OnBehalfOf | None,
                    subject: SubjectRef | None) -> dict[str, str]:
        raise NotImplementedError


def make_views(authz: HandoffAuthz | None = None, keys: FakeKeyProvider | None = None) -> ViewService:
    return ViewService(keys or FakeKeyProvider.default(), authz or HandoffAuthz(), FakeClock(),
                       FieldClassifier(CATALOG))


DOC = "1023456789"

__all__ = ["DOC", "NOW"]


def make_state(**over: Any) -> RunState:
    """Run abierto con hechos, slots, decisiones y acciones ya invalidadas (como lo entrega M4)."""
    base: dict[str, Any] = {
        "active_flow": {"flow": "disputa-cargo@1.0.0", "node_id": "responder"},
        "facts": {
            "cliente": {
                "fact_id": "fact-0001",
                "value": {"document_number": DOC, "first_name": "Ana"},
                "source": {"kind": "tool", "ref": "get_customer@1.0.0"},
                "ts": NOW,
            },
            "cargo": {
                "fact_id": "fact-0002",
                "value": {"amount": Decimal("120.50"), "currency": "USD"},
                "source": {"kind": "tool", "ref": "get_charge@1.0.0", "inputs": ["fact-0001"]},
                "ts": NOW,
            },
            "politica_pagina": {
                "fact_id": "fact-0003",
                "value": {"status": "vigente"},
                "source": {"kind": "knowledge", "ref": "disputas/plazos@snap-1.0.0"},
                "ts": NOW,
            },
        },
        "slots": {
            "documento": {"value": DOC, "status": "claimed", "source_turn": 1},
            "motivo": {"value": "no reconozco el cargo", "status": "validated", "source_turn": 2},
        },
        "decisions": {
            "coincide": {
                "decision_id": "decision-0001", "value": {"match": "unica"}, "p_cal": {"match": 0.93},
                "provider_used": "classifier", "model_version": "clf-demo-1",
            }
        },
        "actions": [
            action(action_id="action-0001", confirm_node_id="c1", state="verified"),
            action(action_id="action-0002", confirm_node_id="c2", state="uncertain"),
            action(action_id="action-0003", confirm_node_id="c3", state="cancelled",
                   cancel_reason="escalated"),
        ],
        "open_questions": ["¿fecha exacta del cargo?"],
        "turn_count": 3,
    }
    return run_state(**(base | over))


def tool_called(call_id: str, *, turn_id: str = "turn-0001") -> EngineEvent:
    return ToolCalled(
        event_id=f"event-{call_id}", run_id="run-0001", turn_id=turn_id, release="rel-2026-09-28", ts=NOW,
        payload=ToolCalledPayload(node_id="n1", tool=EntityRef(id="get_charge", version="1.0.0"),
                                  call_id=call_id, status="ok", args={}, latency_ms=3),
    )


def rule_evaluated(policy: str | None, *, turn_id: str = "turn-0001") -> EngineEvent:
    return RuleEvaluated(
        event_id=f"event-rule-{policy}", run_id="run-0001", turn_id=turn_id, release="rel-2026-09-28", ts=NOW,
        payload=RuleEvaluatedPayload(node_id="n2", policy=EntityRef.parse(policy) if policy else None,
                                     inputs={}, result=True),
    )
