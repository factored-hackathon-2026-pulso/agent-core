"""`scripts/e2e/report.py`: las métricas salen de los eventos reales de un run, sin base de datos."""

import importlib.util
import json
from decimal import Decimal
from pathlib import Path

from agent_core.domain import dumps
from agent_core.ports import GenerationResult
from testing.engine_world import EngineWorld, transfer_calibration
from testing.fakes.gateway import ScriptedGateway

ROOT = Path(__file__).parents[2]
E2E = ROOT / "tests" / "fixtures" / "registry-e2e"
_spec = importlib.util.spec_from_file_location("e2e_report", ROOT / "scripts" / "e2e" / "report.py")
assert _spec is not None and _spec.loader is not None
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def _gen(output: dict[str, object]) -> GenerationResult:
    return GenerationResult(output=output, tokens_in=50, tokens_out=20, cost_usd=Decimal("0.0003"),
                            model="modelo-sintetico-1")


def test_the_report_counts_turns_tools_agent_steps_and_cost_of_a_copilot_run() -> None:
    gateway = ScriptedGateway()
    w = EngineWorld(registry_root=E2E, releases=("copiloto-demo",), agent="copiloto-asesor", gateway=gateway,
                    calibrations={"cal-transfer-demo": transfer_calibration()})
    w.start()
    gateway.push(_gen({"kind": "tool_call", "tool": "leer_productos@1.0.0", "args": {}}))
    gateway.push(_gen({"kind": "final", "output": {"resumen": "Tiene saldo.", "datos": []}}))
    gateway.push(_gen({"text": "Tiene saldo.", "citations": []}))
    w.understands("continue")
    turn = w.turn("¿cuánto debe?")
    events = [json.loads(dumps(e)) for e in w.audit.read(turn.run_id)]

    metrics = report.summarize(report.compute(events))["copiloto-asesor"]

    assert (metrics["runs"], metrics["runs_open"], metrics["turns"]) == (1, 1, 2)
    assert metrics["agent_steps"] == {"tool": 1, "final": 1}
    assert metrics["tools"] == {"leer_productos:ok": 1}
    assert metrics["commands"] == {"continue": 1}
    assert metrics["llm_calls"] >= 1 and Decimal(metrics["cost_usd"]) > 0
    assert any("tool_called" in line for line in report.timeline(events))
