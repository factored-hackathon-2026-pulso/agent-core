"""Prueba de humo del gateway (spec §7): `run_smoke`, su reporte y el subcomando `llm-smoke`."""

import logging
from decimal import Decimal

import pytest

from agent_core.adapters.llm import LLMAgentPort
from agent_core.adapters.llm.smoke import (
    SMOKE_AGENT_PROMPT,
    SMOKE_PROMPT,
    SMOKE_TOOL,
    AgentStepUsage,
    SmokeRegistry,
    format_report,
    percentile,
    run_smoke,
)
from agent_core.audit.replay.cli_support import USAGE_ERROR
from agent_core.cli import _agent_step_runner, main
from agent_core.domain import (
    GatewayError,
    GatewayErrorKind,
    JsonValue,
    ModelProfile,
    Prompt,
    SchemaError,
    ToolDef,
)
from agent_core.ports import GenerationResult
from testing.fakes.clock import FakeClock
from testing.fakes.gateway import ScriptedGateway, gen
from testing.fakes.registry import InMemoryRegistry
from tests.u05.helpers import PROFILE, install_llm_entities

pytest_plugins = ["tests.support.otel"]
pytestmark = pytest.mark.usefixtures("root_logging")  # `llm-smoke` quiets the SDK loggers process-wide

ENDPOINTS = '{"o": {"base_url": "https://x.test/v1", "api_key_env": "K"}}'


def test_percentile_uses_the_nearest_rank() -> None:
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert percentile(values, 50) == 50 and percentile(values, 95) == 100 and percentile([], 50) == 0


def test_run_smoke_counts_ok_errors_tokens_and_cost() -> None:
    script = [gen("hola", []) for _ in range(8)] + [
        GatewayError(GatewayErrorKind.invalid_output, tokens_in=5, tokens_out=1, cost_usd=Decimal("0.0001")),
        GatewayError(GatewayErrorKind.timeout)]
    report = run_smoke(ScriptedGateway(script), SMOKE_PROMPT, FakeClock(), n=10)
    assert (report.calls, report.ok) == (10, 8)
    assert report.errors == {"invalid_output": 1, "timeout": 1}
    assert report.tokens_in == 8 * 10 + 5 and report.tokens_out == 8 * 5 + 1
    assert report.cost_usd == Decimal("0.008") + Decimal("0.0001")
    text = format_report(report)
    assert "invalid_output" in text and "p50" in text and "p95" in text


def test_run_smoke_alternates_es_and_pt() -> None:
    gateway = ScriptedGateway([gen("x", []) for _ in range(4)])
    run_smoke(gateway, SMOKE_PROMPT, FakeClock(), n=4)
    assert [c.locale for c in gateway.calls] == ["es", "pt", "es", "pt"]


def test_the_smoke_registry_serves_a_synthetic_prompt_that_uses_the_profile() -> None:
    inner = InMemoryRegistry()
    install_llm_entities(inner)
    registry = SmokeRegistry(inner, PROFILE)
    prompt = registry.get(SMOKE_PROMPT, Prompt)
    assert str(prompt.model_profile.require_exact()) == "perfil@1.0.0" and set(prompt.locales) == {"es", "pt"}
    assert registry.get(PROFILE, ModelProfile).model == "vendor/modelo-x"  # lo demás va al registro real


def test_llm_smoke_without_endpoints_is_a_usage_error(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("LLM_ENDPOINTS", raising=False)
    code = main(["llm-smoke", "--registry", str(tmp_path), "--profile", "perfil@1.0.0"])
    assert code == USAGE_ERROR and "LLM_ENDPOINTS" in capsys.readouterr().err


def test_llm_smoke_rejects_a_non_exact_profile(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LLM_ENDPOINTS", ENDPOINTS)
    code = main(["llm-smoke", "--registry", str(tmp_path), "--profile", "perfil"])
    assert code == USAGE_ERROR and "id@versión" in capsys.readouterr().err


def _step(output: JsonValue) -> GenerationResult:
    return GenerationResult(output=output, tokens_in=10, tokens_out=5, cost_usd=Decimal("0.001"),
                            model="scripted-1")


def _usage(tokens: int = 15) -> AgentStepUsage:
    return AgentStepUsage(tokens=tokens, cost_usd=Decimal("0.001"))


def test_run_smoke_counts_invalid_agent_steps_apart_from_other_errors() -> None:
    outcomes: list[AgentStepUsage | GatewayError] = [
        _usage(), GatewayError(GatewayErrorKind.invalid_output, tokens_in=4, tokens_out=1,
                               cost_usd=Decimal("0.0002")),
        GatewayError(GatewayErrorKind.timeout)]
    seen: list[tuple[int, str]] = []

    def runner(step: int, locale: str) -> AgentStepUsage:
        seen.append((step, locale))
        outcome = outcomes[step - 1]
        if isinstance(outcome, GatewayError):
            raise outcome
        return outcome

    report = run_smoke(ScriptedGateway([gen("x", [])]), SMOKE_PROMPT, FakeClock(), n=1, agent_step=runner)
    assert seen == [(1, "es"), (2, "es"), (3, "es")]
    assert (report.agent_steps, report.agent_ok, report.agent_invalid, report.agent_other_errors) == (
        3, 1, 1, 1)
    assert report.agent_tokens == 15 + 5 and report.agent_cost_usd == Decimal("0.0012")
    assert "pasos agent: 3  ok: 1  inválidos: 1 (33%)  otros errores: 1" in format_report(report)


def test_run_smoke_without_an_agent_runner_has_no_agent_line() -> None:
    report = run_smoke(ScriptedGateway([gen("x", [])]), SMOKE_PROMPT, FakeClock(), n=1)
    assert report.agent_steps == 0 and "pasos agent" not in format_report(report)


def test_the_smoke_registry_serves_the_agent_prompt_and_tool_bound_to_the_profile() -> None:
    inner = InMemoryRegistry()
    install_llm_entities(inner)
    registry = SmokeRegistry(inner, PROFILE)
    prompt = registry.get(SMOKE_AGENT_PROMPT, Prompt)
    assert str(prompt.model_profile.require_exact()) == "perfil@1.0.0" and "tool_call" in prompt.locales["es"]
    tool = registry.get(SMOKE_TOOL, ToolDef)
    assert tool.description and tool.args_schema is not None and not tool.is_write
    with pytest.raises(SchemaError):
        registry.get(SMOKE_TOOL, Prompt)


def test_the_agent_steps_run_through_llm_agent_port_and_the_synthetic_entities() -> None:
    inner = InMemoryRegistry()
    install_llm_entities(inner)  # perfil prompted
    registry = SmokeRegistry(inner, PROFILE)
    gateway = ScriptedGateway([
        _step({"kind": "tool_call", "tool": str(SMOKE_TOOL), "args": {"consulta": "x"}}),
        _step({"kind": "final"})])  # el segundo es un paso inválido
    port = LLMAgentPort(gateway, registry, lambda kind, ref: ref.require_exact())
    runner = _agent_step_runner(port, FakeClock())
    assert runner(1, "es").tokens == 15
    with pytest.raises(GatewayError) as caught:
        runner(2, "es")
    assert caught.value.kind is GatewayErrorKind.invalid_output
    assert [str(c.prompt) for c in gateway.calls] == [str(SMOKE_AGENT_PROMPT)] * 2
    assert gateway.calls[0].inputs["tools"][0]["tool"] == str(SMOKE_TOOL)  # type: ignore[index]


def test_llm_smoke_with_an_unknown_profile_is_a_clean_usage_error(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LLM_ENDPOINTS", ENDPOINTS)
    code = main(["llm-smoke", "--registry", str(tmp_path), "--profile", "perfil@1.0.0"])
    err = capsys.readouterr().err
    assert code == USAGE_ERROR and "perfil@1.0.0" in err and "Traceback" not in err


def test_llm_smoke_rejects_n_below_one(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LLM_ENDPOINTS", ENDPOINTS)
    code = main(["llm-smoke", "--registry", str(tmp_path), "--profile", "perfil@1.0.0", "--n", "0"])
    assert code == USAGE_ERROR and "--n" in capsys.readouterr().err


def test_llm_smoke_silences_the_sdk_loggers(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("LLM_ENDPOINTS", raising=False)
    logging.getLogger("openai").setLevel(logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    main(["llm-smoke", "--registry", str(tmp_path), "--profile", "perfil@1.0.0"])
    assert logging.getLogger("openai").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
