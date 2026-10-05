"""Interfaz pública de M8 y sus fronteras (contrato `response` de `.importlinter`)."""

import subprocess
import sys

import agent_core.response as response

PUBLIC = [
    "CHECKS", "SUGGESTIONS_SCHEMA", "Draft", "Failure", "NumberFormat", "Responder", "ResponderContext",
    "SuggestOutcome", "Suggester", "SuggesterContext", "TemplateUnavailable", "ToolEntry",
    "ValidationContext", "ValidationResult", "parse_draft", "validate",
]
FORBIDDEN = [
    "flows", "interpreter", "actions", "turn", "handoff", "audit", "decision", "adapters", "registry",
]


def test_public_interface_is_exactly_the_spec() -> None:
    assert sorted(response.__all__) == sorted(PUBLIC)
    assert [name for name in PUBLIC if not hasattr(response, name)] == []


def test_importing_m8_does_not_load_forbidden_modules() -> None:
    code = (
        "import sys, agent_core.response\n"
        f"bad = [m for m in {FORBIDDEN!r} if f'agent_core.{{m}}' in sys.modules]\n"
        "print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_scripted_gateway_lives_in_testing_not_in_the_package() -> None:
    assert not hasattr(response, "ScriptedGateway")
