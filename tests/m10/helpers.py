"""Ayudantes de las pruebas de M10. Solo datos sintéticos."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from agent_core.domain import (
    Agent,
    EngineEvent,
    EntityRef,
    EscalationRequest,
    Message,
    OnBehalfOf,
    OutboxMessage,
    Principal,
    PrincipalType,
    RuleEvaluated,
    RuleEvaluatedPayload,
    RunState,
    SubjectRef,
    Template,
    ToolCalled,
    ToolCalledPayload,
)
from agent_core.handoff.service import HandoffService
from agent_core.ports import AuthzDecision
from agent_core.views import DEFAULT_CATALOG, FieldClassifier, FieldRule, ViewService
from testing.builders import NOW, action, run_state
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.registry import InMemoryRegistry
from testing.fakes.storage import InMemoryStore

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


def make_agent(**over: Any) -> Agent:
    refs = {name: f"{name}@1.0.0" for name in
            ("clarify", "abstain", "handoff", "pending_ack", "pending_offer", "unsupported_language",
             "input_too_large")}
    base: dict[str, Any] = {
        "id": "atencion", "version": "1.0.0", "mode": "conversational", "entry_flow": "disputa-cargo@1.0.0",
        "invocable_by": ["customer"], "min_auth_level": "session", "subject_kinds": ["customer"],
        "supported_locales": ["es", "pt"], "default_locale": "es", "budgets": {
            "max_nodes_per_turn": 20, "max_model_calls_per_turn": 5, "max_tokens_per_run": 10000,
            "max_cost_per_run": Decimal("1.00"), "max_wall_ms_per_turn": 20000},
        "templates": refs, "max_clarifications": 2, "on_clarify_exhausted": "escalate",
        "default_target_queue": "general",
    }
    return Agent.model_validate(base | over)


HANDOFF_TEMPLATE = Template(id="handoff", version="1.0.0", locales={
    "es": "Te paso con una persona del equipo de disputas.",
    "pt": "Vou passar você para a equipe de disputas."})


@dataclass
class World:
    store: InMemoryStore
    registry: InMemoryRegistry
    ids: FakeIds
    clock: FakeClock
    authz: HandoffAuthz
    service: HandoffService


def make_world(*, authz: HandoffAuthz | None = None, with_template: bool = True,
               views: ViewService | None = None, record: Any = None) -> World:
    store = InMemoryStore()
    registry = InMemoryRegistry()
    registry.add(make_agent(), *([HANDOFF_TEMPLATE] if with_template else []))
    keys = FakeKeyProvider.default()
    authz = authz or HandoffAuthz()
    clock = FakeClock()
    ids = FakeIds()
    kwargs = {} if record is None else {"record": record}
    service = HandoffService(uow_factory=store.uow, registry=registry, views=views or make_views(authz, keys),
                             authz=authz, keys=keys, clock=clock, ids=ids, **kwargs)
    return World(store, registry, ids, clock, authz, service)


def seed_run(world: World, state: RunState) -> RunState:
    """Deja el run guardado como M4 lo tendría antes del turno (versión 1)."""
    with world.store.uow() as uow:
        saved = uow.save_run(state, expected_version=0)
        uow.commit()
    return saved


def escalate_and_commit(world: World, state: RunState, request: EscalationRequest,
                        events: list[EngineEvent] | None = None
                        ) -> tuple[RunState, list[EngineEvent], OutboxMessage, Message]:
    """Lo que hará M4: una transacción con el paquete, el run cerrado, los eventos y el outbox."""
    with world.store.uow() as uow:
        result = world.service.escalate(state, request, events or [], uow=uow, turn_id="turn-0001")
        closed, new_events, outbox, _message = result
        uow.save_run(closed, expected_version=state.state_version)
        uow.append_events(state.run_id, new_events)
        uow.enqueue_outbox(outbox)
        uow.commit()
    return result
