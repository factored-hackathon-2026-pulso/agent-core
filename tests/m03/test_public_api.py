"""Interfaz pública de M3: lo que importan M2 y M4 (M3 §2)."""

import agent_core.actions as actions

EXPECTED = {
    "TRANSITIONS", "VERIFIABLE", "ActionContext", "ActionManager", "Answer", "AnswerResult", "AuditProjector",
    "EventRecorder", "PredicateEvaluator", "RedactAll", "RefResolver", "TemplateRenderer", "Trigger",
    "VerifyResult", "WriteResult", "append_events", "exact_ref", "next_state",
}


def test_public_api() -> None:
    assert set(actions.__all__) == EXPECTED
    for name in EXPECTED:
        assert getattr(actions, name) is not None
