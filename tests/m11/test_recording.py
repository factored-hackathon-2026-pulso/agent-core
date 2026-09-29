"""Grabación para `agentcore record`: captura resultados `full` y borradores sin alterar el puerto."""

from decimal import Decimal

from agent_core.audit.chain import chain_events
from agent_core.audit.replay.recording import RecordingGateway, RecordingToolExecutor, build_fixture
from agent_core.domain import EntityRef
from agent_core.ports import GenerationResult, ToolCallContext, ToolResult
from testing.builders import principal
from tests.m11.helpers import event


class InnerTools:
    def execute(self, tool, args, bound_params, ctx, idempotency_key=None):  # type: ignore[no-untyped-def]
        return ToolResult(status="ok", result_full={"monto": Decimal("10.50")}, source="tx",
                          call_id="call-0001")

    def definition(self, tool):  # type: ignore[no-untyped-def]
        raise NotImplementedError


class InnerGateway:
    def generate(self, prompt, inputs_model_view, locale, schema=None):  # type: ignore[no-untyped-def]
        return GenerationResult(output="hola", tokens_in=1, tokens_out=1, cost_usd=Decimal("0"), model="m")


def test_recording_wrappers_capture_and_pass_through() -> None:
    tools, llm = RecordingToolExecutor(InnerTools()), RecordingGateway(InnerGateway())  # type: ignore[arg-type]
    ctx = ToolCallContext(run_id="run-0001", release="r", principal=principal())
    assert tools.execute(EntityRef.parse("t@1.0.0"), {}, {}, ctx).call_id == "call-0001"
    assert llm.generate(EntityRef.parse("p@1.0.0"), {}, "es").output == "hola"
    events = chain_events("run-0001", [event("run_started"), event("run_closed", n=2)], None)
    fx = build_fixture("demo", "run-0001", "rel-2026-09-28", events, [{"text_model": "hola"}], tools, llm)
    assert fx.full["call-0001"].result_full == {"monto": Decimal("10.50")} and fx.drafts == ["hola"]
    assert fx.events == events
