"""Un payload de ejemplo por tipo de evento (datos sintéticos)."""

from decimal import Decimal
from typing import Any

TS = "2026-09-28T12:00:00Z"
FP = {"alg": "HMAC-SHA256", "kid": "demo-fp-1", "value": "ab" * 32}

SAMPLE_PAYLOADS: dict[str, dict[str, Any]] = {
    "run_started": {"agent": "atencion@1.0.0", "mode": "conversational", "subject_kind": "customer",
                    "principal_type": "customer", "locale": "es", "reportable_attrs": {"country": "CO"}},
    "turn_started": {"client_turn_id": "c-1", "guards": {
        "lang": {"detector": "lingua@1.3.5", "letters": 20,
                 "top2": [{"lang": "es", "score": 0.9}, {"lang": "pt", "score": 0.1}],
                 "decision": "kept", "locale_prior": "es", "locale": "es"},
        "injection": {"flagged": False, "signals": [], "ruleset": "injection-rules@1.0.0"},
        "size_ok": True}},
    "command_emitted": {"command": "start_flow", "flow": "disputa-cargo", "interrupt": None,
                        "additional_flows": [], "above_threshold": {"command": True, "flow": True},
                        "decision_id": "decision-0001", "source": "understand"},
    "node_entered": {"flow": "disputa-cargo@1.0.0", "node_id": "pedir_cargo", "node_type": "collect",
                     "resume_kind": "none"},
    "decision_made": {"decision_id": "decision-0001", "model": "match-cargo@2.0.0",
                      "provider_used": "classifier", "model_version": "clf-demo-1", "fallback_depth": 1,
                      "value": {"match": "unica"}, "p_cal": {"match": 0.91}, "p_raw": {"match": 0.88},
                      "top_k": {"match": [{"label": "unica", "p": 0.91}]}, "above_threshold": {"match": True},
                      "latency_ms": 12, "tokens": 0, "cost_usd": "0.0000", "locale": "es"},
    "rule_evaluated": {"node_id": "umbral", "policy": "escalamiento-disputa-monto@1.0.0",
                       "inputs": {"facts.monto_usd.value": Decimal("120.50")}, "result": False},
    "tool_called": {"node_id": "buscar_tx", "tool": "buscar_transacciones@1.0.0", "call_id": "call-0001",
                    "status": "ok", "args": {"texto": "⟦txt:1⟧"}, "result": {"count": 2}, "result_fp": FP,
                    "error": None, "attempt": 1, "action_id": None, "latency_ms": 35},
    "agent_step": {"node_id": "investigar", "step": 1, "kind": "tool", "tool": "buscar_transacciones@1.0.0",
                   "call_id": "call-0001", "status": "ok", "text_fp": None, "latency_ms": 41},
    "knowledge_read": {"node_id": "saber", "purpose": "customer_answer", "result": "ok",
                       "refs": ["faq/cargos.md@kb-base@1.0.0#plazos"], "filtered_out": [], "missing": [],
                       "reason": None},
    "step_up_requested": {"node_id": "radicar", "required_level": "step_up", "attempt": 1},
    "action_confirmed": {"action_id": "action-0001", "source": "button"},
    "action_cancelled": {"action_id": "action-0001", "reason": "token_expired"},
    "action_dispatched": {"action_id": "action-0001", "tool": "radicar_pqr@1.0.0", "args_fp": None},
    "action_verified": {"action_id": "action-0001", "result": "verified", "readback_call_id": "call-0002"},
    "expiry_evaluated": {"now": TS, "last_activity_at": "2026-09-28T11:00:00Z", "ttl": "PT30M",
                         "expired": True},
    "response_emitted": {"node_id": "responder_ok", "kind": "generated",
                         "validator": {"ok": True, "failures": [], "regenerations": 0},
                         "fallback_used": False,
                         "claims": ["confirmar"], "transcript_fp": FP,
                         "llm": {"calls": 1, "latency_ms": 800, "tokens_in": 300, "tokens_out": 120,
                                 "cost_usd": "0.0021", "cost_known": True, "models": ["llm-demo"]}},
    "response_failed": {"node_id": "responder_ok", "reason_code": "validation_failed",
                        "validator": {"ok": False, "failures": ["citations"], "regenerations": 1},
                        "claims": ["confirmar"],
                        "llm": {"calls": 2, "latency_ms": 1600, "tokens_in": 600, "tokens_out": 240,
                                "cost_usd": "0.0042", "cost_known": True, "models": ["llm-demo"]}},
    "turn_completed": {"client_turn_id": "c-1", "entry": "turn", "duration_ms": 950,
                       "stages": {"guards_ms": 3, "understand_ms": 120, "flow_ms": 60, "response_ms": 700},
                       "degraded": False, "awaiting": "none"},
    "injection_flagged": {"signals": ["ignore-instructions"], "ruleset": "injection-rules@1.0.0",
                          "scope": "user_text"},
    "access_denied": {"reason": "principal_mismatch", "tool": None},
    "escalated": {"reason_code": "policy:escalamiento-disputa-monto", "target_queue": "disputas",
                  "priority": "high", "handoff_ref": "handoff-0001"},
    "handoff_resolved": {"handoff_ref": "handoff-0001", "resolution_code": "resuelto",
                         "handoff_quality": "useful", "reader_type": "advisor"},
    "run_closed": {"outcome": "escalated", "closed_by": "escalation"},
}


def make_event(event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event_id": "event-0001",
        "type": event_type,
        "run_id": "run-0001",
        "turn_id": "turn-0001",
        "session_id": "session-0001",
        "release": "rel-2026-09-28",
        "ts": TS,
        "payload": SAMPLE_PAYLOADS[event_type] if payload is None else payload,
    }


def reverse_keys(value: Any) -> Any:
    """Mismo dato con las claves de cada objeto en orden inverso."""
    if isinstance(value, dict):
        return {k: reverse_keys(v) for k, v in reversed(list(value.items()))}
    if isinstance(value, list):
        return [reverse_keys(v) for v in value]
    return value
