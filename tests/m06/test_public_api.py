"""Interfaz pública de M6: lo que importan M4 y M8 (M6 §2)."""

import agent_core.guards as guards

EXPECTED = {
    "UNCALIBRATED", "GuardResult", "GuardService", "GuardsConfigError", "InjectionResult", "LangDecision",
    "LangThresholds", "detect_language", "scan_injection",
}


def test_public_api() -> None:
    assert set(guards.__all__) == EXPECTED
    for name in EXPECTED:
        assert getattr(guards, name) is not None
