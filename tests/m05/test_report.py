import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from agent_core.decision.calibration.artifact import CalibrationArtifact, Target
from agent_core.decision.calibration.calibrate import DevExample, calibrate
from agent_core.decision.calibration.report import (
    build_report,
    load_events,
    render_json,
    render_markdown,
    runtime_metrics,
)
from agent_core.decision.types import RawPrediction
from agent_core.domain import DecisionMade, dumps
from testing.fakes.provider import Reply, ScriptedProvider, Timeout
from tests.m05.helpers import make_service, model_def, ref, scope

ROOT = Path(__file__).resolve().parents[2]
TARGETS = {"command": Target(metric="precision", value=0.8)}


def _examples(lang: str, n: int, synthetic: bool = False) -> list[DevExample]:
    return [DevExample(id=f"{lang}-{i:02d}", inputs={"text": f"texto {lang} {i}"},
                       labels={"command": "affirm" if i % 3 else "deny"}, lang=lang, synthetic=synthetic)
            for i in range(n)]


def _artifact() -> CalibrationArtifact:
    provider = ScriptedProvider("classifier")
    for n in (12, 9):
        for i in range(n):
            p = 0.3 + 0.05 * i if i % 3 else 0.2 + 0.02 * i
            provider.push(RawPrediction(value={"command": "affirm"}, p_raw={"command": p}))
    return calibrate(model_def(), _examples("es", 12) + _examples("pt", 9, synthetic=True),
                     {"classifier": provider}, targets=TARGETS, min_samples={"es": 1, "pt": 5}, min_support=3)


def test_calibrate_fills_metrics_as_canonical_strings() -> None:
    art = _artifact()
    languages = art.metrics["languages"]
    assert isinstance(languages, dict) and set(languages) == {"es", "pt"}
    es = languages["es"]
    assert isinstance(es, dict) and es["synthetic"] is False and es["samples"] == 12
    entry = es["providers"]["classifier"]["command"]  # type: ignore[index]
    assert set(entry) == {"n", "ece", "macro_f1", "precision_at_threshold", "coverage"}
    assert all(isinstance(entry[k], str) for k in ("ece", "macro_f1", "coverage"))
    assert languages["pt"]["synthetic"] is True  # type: ignore[index]
    assert CalibrationArtifact.from_json(art.to_json()) == art  # las métricas sobreviven al JSON


def test_recall_metric_only_for_recall_targets() -> None:
    provider = ScriptedProvider("classifier")
    for i in range(9):
        provider.push(RawPrediction(value={"command": "affirm"}, p_raw={"command": 0.5 + 0.05 * i}))
    art = calibrate(model_def(), _examples("es", 9), {"classifier": provider},
                    targets={"command": Target(metric="recall", value=0.5)}, min_samples={"es": 1},
                    min_support=3)
    entry = art.metrics["languages"]["es"]["providers"]["classifier"]["command"]  # type: ignore[index]
    assert "recall_at_threshold" in entry


def test_markdown_has_one_table_per_language_and_provider_and_marks_synthetic_pt() -> None:
    text = render_markdown(build_report(_artifact()))
    for column in ("ECE", "macro-F1", "precision@thr", "coverage"):
        assert column in text
    assert "## es" in text and "## pt (synthetic)" in text
    assert text.count("| command |") == 2
    assert "### classifier" in text


def test_json_report_is_deterministic() -> None:
    a, b = _artifact(), _artifact()
    assert render_json(build_report(a)) == render_json(build_report(b))
    assert render_markdown(build_report(a)) == render_markdown(build_report(b))


def _events() -> list[DecisionMade]:
    definition = model_def(providers=("jev", "classifier"), calibration_method="none", calibration_run=None,
                           thresholds_from=None)
    rig = make_service(definition)
    events: list[DecisionMade] = []
    for latency, fallback in ((10, False), (20, False), (30, True), (40, False)):
        if fallback:
            rig.providers["jev"].push(Timeout())
            rig.providers["classifier"].push(RawPrediction(
                value={"command": "affirm"}, p_raw={"command": 0.9}, cost_usd=Decimal("0.5")))
        else:
            rig.providers["jev"].push(Reply(RawPrediction(
                value={"command": "affirm"}, p_raw={"command": 0.9}, cost_usd=Decimal("0.25")), latency))
        events.append(rig.service.decide(ref(definition), {"text": "x"}, "es", rig.vault, scope=scope())[1])
    return events


def test_runtime_metrics_by_language_and_provider() -> None:
    events = _events()
    metrics = runtime_metrics(events)
    jev = metrics["es"]["jev"]  # type: ignore[index]
    assert jev == {"decisions": 3, "latency_p50_ms": 20, "latency_p95_ms": 40, "cost_usd": "0.75",
                   "fallback_rate": "0.000000"}
    classifier = metrics["es"]["classifier"]  # type: ignore[index]
    assert classifier["decisions"] == 1 and classifier["fallback_rate"] == "1.000000"


def test_events_round_trip_through_jsonl() -> None:
    events = _events()
    text = "\n".join(dumps(e) for e in events) + "\n"
    assert load_events(text) == events


def test_report_with_events_includes_the_runtime_section() -> None:
    text = render_markdown(build_report(_artifact(), _events()))
    assert "## Runtime" in text and "p95" in text


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "agent_core.decision", "report", *args], cwd=ROOT,
                          capture_output=True, text=True, check=False)


def test_cli_prints_the_report_and_is_deterministic(tmp_path: Path) -> None:
    artifact_path = tmp_path / "cal.json"
    artifact_path.write_text(_artifact().to_json(), encoding="utf-8")
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("\n".join(dumps(e) for e in _events()) + "\n", encoding="utf-8")
    first = _cli("--artifact", str(artifact_path), "--events", str(events_path))
    second = _cli("--artifact", str(artifact_path), "--events", str(events_path))
    assert first.returncode == 0, first.stderr
    for column in ("ECE", "macro-F1", "precision@thr", "coverage"):
        assert column in first.stdout
    assert first.stdout == second.stdout
    assert "texto es" not in first.stdout  # nunca imprime textos de entrada


def test_cli_json_format_and_out_file(tmp_path: Path) -> None:
    artifact_path = tmp_path / "cal.json"
    artifact_path.write_text(_artifact().to_json(), encoding="utf-8")
    out = tmp_path / "report.json"
    result = _cli("--artifact", str(artifact_path), "--format", "json", "--out", str(out))
    assert result.returncode == 0 and result.stdout == ""
    assert '"run_id"' in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("content", ["no es json", "[1]", '{"run_id": "x"}'])
def test_cli_unreadable_artifact_exits_2_without_leaking_content(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    result = _cli("--artifact", str(path))
    assert result.returncode == 2 and result.stdout == ""
    assert "no se pudo leer" in result.stderr and content not in result.stderr


def test_cli_missing_file_exits_2(tmp_path: Path) -> None:
    assert _cli("--artifact", str(tmp_path / "no-existe.json")).returncode == 2
