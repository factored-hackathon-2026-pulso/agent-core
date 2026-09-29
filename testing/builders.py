"""Constructores de datos sintéticos para pruebas de todos los módulos. Nunca datos reales del dataset."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from agent_core.domain.identity import OnBehalfOf, Principal
from agent_core.domain.state import Action, RunState

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def principal(**over: Any) -> Principal:
    base: dict[str, Any] = {
        "type": "customer",
        "id": "cust-001",
        "attrs": {"country": "CO", "segment": "demo"},
        "auth": {"level": "session", "at": NOW},
        "exp": NOW + timedelta(hours=1),
    }
    return Principal.model_validate(base | over)


def advisor_with_delegation() -> tuple[Principal, OnBehalfOf]:
    advisor = principal(type="advisor", id="adv-7", attrs={})
    obo = OnBehalfOf.model_validate(
        {
            "subject": {"kind": "customer", "ref": "cust-001"},
            "grant_ref": "grant-9",
            "grantee": {"type": "advisor", "id": "adv-7"},
            "scopes": ["read"],
            "exp": NOW + timedelta(hours=1),
        }
    )
    return advisor, obo


def action(**over: Any) -> Action:
    base: dict[str, Any] = {
        "action_id": "action-0001",
        "confirm_node_id": "confirmar",
        "flow": "disputa-cargo@1.0.0",
        "tool": "radicar_pqr@1.0.0",
        "args": {"transaction_id": "tx-demo-1", "monto": Decimal("500.00")},
        "args_hash": "0" * 64,
        "state": "proposed",
        "confirmation_token_hash": "f" * 64,
        "token_exp": NOW + timedelta(minutes=5),
        "idempotency_key": "action-0001",
        "created_at": NOW,
    }
    return Action.model_validate(base | over)


def run_state(**over: Any) -> RunState:
    base: dict[str, Any] = {
        "run_id": "run-0001",
        "session_id": "session-0001",
        "release": "rel-2026-09-28",
        "agent": "atencion@1.0.0",
        "principal": principal(),
        "subject": {"kind": "customer", "ref": "cust-001"},
        "mode": "conversational",
        "locale": "es",
        "created_at": NOW,
        "last_activity_at": NOW,
        "inactive_after": NOW + timedelta(minutes=30),
    }
    return RunState.model_validate(base | over)


def full_run_state() -> RunState:
    advisor, obo = advisor_with_delegation()
    return run_state(
        principal=advisor,
        on_behalf_of=obo,
        awaiting="confirmation",
        awaiting_node_id="confirmar",
        active_flow={"flow": "disputa-cargo@1.0.0", "node_id": "confirmar", "local_slots": {"k": 1}},
        pending_intents=[{"flow": "bloquear-tarjeta", "priority": 80, "mention_order": 1}],
        slots={"descripcion_cargo": {"value": "cargo desconocido", "status": "validated", "source_turn": 1}},
        facts={
            "monto_usd": {
                "fact_id": "fact-0001",
                "value": {"amount": Decimal("120.50"), "currency": "USD", "items": [1, "x", None, True]},
                "source": {"kind": "compute", "ref": "convertir_moneda@1.0.0", "inputs": ["fact-0000"]},
                "ts": NOW,
            }
        },
        decisions={
            "coincide": {
                "decision_id": "decision-0001",
                "value": {"match": "unica", "transaction": "⟦tx:1⟧"},
                "p_cal": {"match": 0.93, "otro": None},
                "provider_used": "classifier",
                "model_version": "clf-demo-1",
            }
        },
        actions=[action(), action(action_id="action-0000", confirm_node_id="otro_confirm", state="verified")],
        token_map={"kid": "demo-tm-1", "nonce": "bm9uY2U=", "ciphertext": "Y2lwaGVy"},
        open_questions=["¿fecha exacta del cargo?"],
        budgets_used={
            "run_tokens": 420,
            "run_cost": Decimal("0.0123"),
            "turn_nodes": 3,
            "turn_model_calls": 1,
            "turn_started_at": NOW,
        },
        turn_count=3,
        clarifications_used=1,
        node_attempts={"confirmar": 1},
        repair_turns_used=1,
        degraded_turns=[2],
    )
