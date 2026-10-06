"""The local state kit and the reference compose give `serve` language thresholds (else pt gets es)."""

import importlib.util
import json
from pathlib import Path

import yaml

from agent_core.guards import LangThresholds

ROOT = Path(__file__).resolve().parents[2]


def _serve_state():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("serve_state", ROOT / "scripts" / "serve_state.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_state_kit_writes_the_language_thresholds_the_fixture_names(tmp_path: Path) -> None:
    _serve_state().write_lang_thresholds(tmp_path)
    doc = json.loads((tmp_path / "lang-thresholds.json").read_text(encoding="utf-8"))
    fixture = yaml.safe_load((ROOT / "tests/fixtures/registry-e2e/language_detection/lang-es-pt@1.0.0.yaml")
                             .read_text(encoding="utf-8"))
    assert LangThresholds.model_validate(doc[fixture["thresholds_from"]])


def test_the_reference_compose_points_serve_at_the_thresholds() -> None:
    compose = yaml.safe_load((ROOT / "deploy/compose/docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["serve"]["environment"]
    assert env["AGENTCORE_LANG_THRESHOLDS"] == "/state/lang-thresholds.json"


def test_every_classifier_decision_model_reads_a_key_of_its_input_view() -> None:
    """A1: serve hands the provider path-keyed views; `text_from` must name one of them."""
    from agent_core.decision import ClassifierProvider
    from agent_core.domain import ProviderSpec
    from testing.serve_classifier import synthetic_classifier_json

    class _Loader:
        def load(self, ref: str) -> str:
            return synthetic_classifier_json()

    found = 0
    for path in sorted((ROOT / "tests/fixtures/registry-e2e/decision_models").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for provider in doc.get("providers", []):
            if provider["provider"] != "classifier":
                continue
            found += 1
            key = provider["config"].get("text_from", "text")
            assert key in doc["input_view"], path.name
            inputs = {k: "disputa cargo duplicado" if k == key else [] for k in doc["input_view"]}
            spec = ProviderSpec(provider="classifier", config=provider["config"])
            result = ClassifierProvider(_Loader()).predict(spec, inputs, {}, "es")  # type: ignore[arg-type]
            assert result.value
    assert found >= 1


def test_serve_state_ships_the_calibration_copiloto_sugerencias_needs() -> None:
    """Without `cal-sugerencias-provisional` no threshold is met and every suggestions run fails."""
    source = (ROOT / "scripts" / "serve_state.py").read_text(encoding="utf-8")
    model = ROOT / "tests" / "fixtures" / "copiloto-sugerencias" / "decision_models"
    lines = (model / "sin-sugerencia@1.0.0.yaml").read_text(encoding="utf-8").splitlines()
    wanted = next(x.split(":", 1)[1].strip() for x in lines if x.startswith("thresholds_from:"))
    assert f'"{wanted}.json"' in source
