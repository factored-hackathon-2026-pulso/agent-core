"""Dataset, runner y reporte de la prueba de humo de JEV, sin red (la real exige AGENT_CORE_JEV_SMOKE=1)."""

import re
from decimal import Decimal

import pytest

from agent_core.decision import JevProvider, ProviderError, ProviderTimeout, RawPrediction
from agent_core.domain import JsonValue, ProviderSpec
from testing.fakes.clock import FakeClock
from tests.m05.smoke.cases import COMMANDS, FLOWS, all_cases
from tests.m05.smoke.run import SCHEMA, LangProbe, Row, evaluate, main, make_specs, render

CASES = all_cases()


def test_dataset_shape_and_labels() -> None:
    by_lang = {lang: [c for c in CASES if c.lang == lang] for lang in ("es", "pt", "mix")}
    assert {k: len(v) for k, v in by_lang.items()} == {"es": 50, "pt": 50, "mix": 10}
    assert len({c.id for c in CASES}) == len(CASES)
    for lang in ("es", "pt"):
        assert {c.command for c in by_lang[lang]} == set(COMMANDS)
        assert {c.flow for c in by_lang[lang] if c.command == "start_flow"} == set(FLOWS)
        assert sum(c.words <= 3 for c in by_lang[lang]) >= 12  # subconjunto de 1 a 3 palabras
    for case in CASES:
        assert case.command in COMMANDS
        assert (case.flow is not None) == (case.command == "start_flow")
        assert case.flow is None or case.flow in FLOWS
        assert (case.ctx == "confirm") == (case.command in {"affirm", "deny"})


def test_dataset_is_synthetic_and_has_no_identifiers() -> None:
    assert len({(c.lang, c.text.lower()) for c in CASES}) == len(CASES)  # sin duplicados
    for case in CASES:
        assert not re.search(r"\d{3,}|@|https?:|\+\d", case.text)  # sin documentos, correos, URLs, teléfonos


def test_inputs_are_the_understand_model_view() -> None:
    confirm = next(c for c in CASES if c.ctx == "confirm" and c.lang == "pt").inputs()
    assert set(confirm) == {"text", "recent_turns", "current_node", "confirm_pending"}
    assert confirm["confirm_pending"] is True and confirm["current_node"] == "confirm"
    plain = next(c for c in CASES if c.ctx == "none").inputs()
    assert plain["recent_turns"] == [] and plain["current_node"] is None and plain["confirm_pending"] is False


class ScriptedJev:
    """Proveedor que acierta salvo en `wrong` (ids) y falla en `fail` (id → excepción)."""
    name = "jev"

    def __init__(self, wrong: set[str] = frozenset(), fail: dict[str, Exception] | None = None) -> None:  # type: ignore[assignment]
        self.wrong, self.fail = wrong, fail or {}
        self.by_text = {c.text: c for c in CASES}

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: str) -> RawPrediction:
        case = self.by_text[str(inputs_model_view["text"])]
        if "language" in (schema.get("properties") or {}):  # type: ignore[operator]
            return RawPrediction(value={"language": "es" if case.lang != "pt" else "pt"},
                                 p_raw={"language": 0.9}, tokens=50, cost_usd=Decimal("0.000001"),
                                 model_version="jev:jev-9.9.9")
        if case.id in self.fail:
            raise self.fail[case.id]
        command = "clarify" if case.id in self.wrong else case.command
        return RawPrediction(value={"command": command, "flow": case.flow or "track_order"},
                             p_raw={"command": 0.8, "flow": 0.7}, tokens=300, cost_usd=Decimal("0.0000126"),
                             model_version="jev:jev-9.9.9")


def probe(text: str) -> LangProbe:
    return LangProbe(top1="pt" if "não" in text or "você" in text else "es", decision="kept", locale="es",
                     letters=len(text), ms=1.0)


def run(provider: object, **kwargs: bool) -> list[Row]:
    return evaluate(provider, make_specs("jev-latest", 5000), FakeClock(), CASES, probe, **kwargs)  # type: ignore[arg-type]


def test_evaluate_scores_each_case_and_isolates_provider_errors() -> None:
    failing = {"es-01": ProviderTimeout(), "es-02": ProviderError("jev: HTTP 429", tokens=7)}
    rows = run(ScriptedJev(wrong={"pt-19"}, fail=failing))
    by_id = {r.case.id: r for r in rows}
    assert len(rows) == 110 and by_id["es-01"].error == "timeout"
    assert by_id["es-02"].error == "jev: HTTP 429" and by_id["es-02"].tokens >= 7
    assert by_id["pt-19"].command == "clarify" and not by_id["pt-19"].command_ok
    assert by_id["es-03"].command_ok and by_id["es-03"].model_version == "jev:jev-9.9.9"
    assert all(r.probe is not None for r in rows)


def test_report_has_every_section_and_no_secrets() -> None:
    rows = run(ScriptedJev(wrong={"pt-19"}, fail={"es-01": ProviderTimeout()}))
    report = render(rows, {429: 3})
    for heading in ("## Precisión", "## Latencia", "## Errores, límites y coste", "## Calibración cruda",
                    "## Idioma", "Portuñol", "429×3", "timeout×1", "jev:jev-9.9.9", "ECE"):
        assert heading in report
    latency_es = report.split("## Latencia")[1].split("## Errores")[0].split("\n")[4]
    assert latency_es.startswith("| es |") and "n/d" not in latency_es
    assert "sk-" not in report and "Bearer" not in report


def test_report_marks_missing_data_instead_of_failing() -> None:
    rows = [Row(case) for case in CASES]  # ningún caso respondió
    assert "n/d" in render(rows, {})


def test_without_the_jev_language_call_nothing_extra_is_sent() -> None:
    rows = run(ScriptedJev(), jev_language=False)
    assert all(r.jev_lang is None and r.jev_lang_ms is None for r in rows)


class EchoTransport:
    """Transporte que responde con la primera opción de cada pregunta: valida el request real de la prueba."""

    def __init__(self) -> None:
        self.requests: list[dict[str, JsonValue]] = []

    def send(self, request: dict[str, JsonValue], timeout_ms: int) -> dict[str, JsonValue]:
        self.requests.append(request)
        questions = request["questions"]
        assert isinstance(questions, dict)
        answers: dict[str, JsonValue] = {}
        for name, question in questions.items():
            assert isinstance(question, dict) and question["type"] == "choice" and question["instructions"]
            options = list(question["criteria"])  # type: ignore[call-overload]
            answers[name] = {"type": "choice", "choice": options[0], "confidence": 0.5,
                             "probabilities": {o: 1 / len(options) for o in options}}
        return {"model": "jev-9.9.9", "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 5}}


def test_smoke_specs_build_valid_requests_with_the_real_adapter() -> None:
    transport = EchoTransport()
    sample = CASES[:3] + CASES[-2:]
    rows = evaluate(JevProvider(transport), make_specs("jev-latest", 5000), FakeClock(), sample, probe)
    assert all(r.error is None and r.jev_lang_error is None for r in rows)
    kinds = [set(r["questions"]) for r in transport.requests]  # type: ignore[call-overload]
    assert kinds == [{"command", "flow"}, {"language"}] * 5
    assert rows[0].model_version == "jev:jev-9.9.9" and rows[0].tokens == 210 and SCHEMA


def test_local_only_mode_needs_neither_key_nor_flag_and_makes_no_jev_calls(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("AGENT_CORE_JEV_SMOKE", raising=False)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    assert main(["--local-only", "--limit", "20"]) == 0
    out = capsys.readouterr().out
    assert "## Idioma" in out and "detector top-1" in out and "## Precisión" not in out


def test_main_refuses_to_call_out_without_the_smoke_flag(monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("AGENT_CORE_JEV_SMOKE", raising=False)
    monkeypatch.setenv("JEV_API_KEY", "sk-sintetica-NO-USAR")
    assert main([]) == 2
    captured = capsys.readouterr()
    assert "AGENT_CORE_JEV_SMOKE=1" in captured.err and "sk-sintetica" not in captured.out + captured.err


def test_main_needs_the_key_and_never_prints_it(monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("AGENT_CORE_JEV_SMOKE", "1")
    monkeypatch.setenv("JEV_API_KEY", "  ")
    assert main([]) == 2
    assert "JEV_API_KEY" in capsys.readouterr().err
