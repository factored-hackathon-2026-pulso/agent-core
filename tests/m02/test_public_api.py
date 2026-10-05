import subprocess
import sys

import agent_core.interpreter as interpreter


def test_public_surface() -> None:
    assert set(interpreter.__all__) == {
        "NO_RESUME", "AgentFinal", "AgentObservation", "AgentPort", "AgentRequest", "AgentStepResult",
        "AgentToolCall", "CircuitBreaker", "DecisionPort", "DecisionResult", "GenerateRequest",
        "GenerateResult",
        "Projector", "ResponderPort", "Resume", "StepContext", "StepOutcome", "Stop", "SuggestRequest",
        "SuggestResult", "SuggesterPort",
        "TransferRequest", "advance", "begin_turn", "evaluate", "start_flow", "truthy",
    }
    for name in interpreter.__all__:
        assert getattr(interpreter, name) is not None


def test_importing_the_interpreter_does_not_import_forbidden_modules() -> None:
    code = ("import sys, agent_core.interpreter; "
            "bad = [m for m in sys.modules if m.split('.')[:2] in "
            "(['agent_core','guards'], ['agent_core','handoff'], ['agent_core','audit'], "
            "['agent_core','turn'], ['agent_core','api'], ['agent_core','registry'], "
            "['agent_core','adapters'])]; "
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], check=True)
