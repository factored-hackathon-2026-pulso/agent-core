"""Prueba de humo del gateway (spec §7): `run_smoke`, su reporte y el subcomando `llm-smoke`."""

from decimal import Decimal

from agent_core.adapters.llm.smoke import SMOKE_PROMPT, SmokeRegistry, format_report, percentile, run_smoke
from agent_core.audit.replay.cli_support import USAGE_ERROR
from agent_core.cli import main
from agent_core.domain import GatewayError, GatewayErrorKind, ModelProfile, Prompt
from testing.fakes.clock import FakeClock
from testing.fakes.gateway import ScriptedGateway, gen
from testing.fakes.registry import InMemoryRegistry
from tests.u05.helpers import PROFILE, install_llm_entities


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
    monkeypatch.setenv("LLM_ENDPOINTS", '{"o": {"base_url": "https://x.test/v1", "api_key_env": "K"}}')
    code = main(["llm-smoke", "--registry", str(tmp_path), "--profile", "perfil"])
    assert code == USAGE_ERROR and "id@versión" in capsys.readouterr().err
