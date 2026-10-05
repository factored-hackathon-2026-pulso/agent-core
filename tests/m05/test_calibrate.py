"""T-M5-09: `calibrate` reproduce el mismo artefacto con el mismo split (determinista)."""

from dataclasses import replace

import pytest

from agent_core.decision.calibration.artifact import Target
from agent_core.decision.calibration.calibrate import DevExample, calibrate
from agent_core.decision.types import DecisionConfigError, RawPrediction
from agent_core.domain import DecisionModelDef, JsonValue, ProviderSpec, sha256_hex
from testing.fakes.provider import Failure, ScriptedProvider
from tests.m05.helpers import make_service, model_def, ref, scope

TARGETS = {"command": Target(metric="precision", value=0.8)}
MIN_SAMPLES = {"es": 1, "pt": 6}


def _examples(lang: str, n: int, *, synthetic: bool = False) -> list[DevExample]:
    return [DevExample(id=f"{lang}-{i:02d}", inputs={"text": f"texto {lang} {i}"},
                       labels={"command": "affirm" if i % 3 else "deny"}, lang=lang, synthetic=synthetic)
            for i in range(n)]


def _script(provider: ScriptedProvider, n: int) -> None:
    """Siempre `affirm`; la confianza sube con `i` y los errores (i % 3 == 0) son los menos confiados."""
    for i in range(n):
        p = 0.3 + 0.05 * i if i % 3 else 0.2 + 0.02 * i
        provider.push(RawPrediction(value={"command": "affirm"}, p_raw={"command": p}))


def _run(split: list[DevExample], *, n_es: int = 12, n_pt: int = 0, min_samples=None, **over):  # type: ignore[no-untyped-def]
    definition = over.pop("definition", model_def())
    provider = ScriptedProvider("classifier")
    _script(provider, n_es)
    _script(provider, n_pt)
    return calibrate(definition, split, {"classifier": provider}, targets=TARGETS,
                     min_samples=min_samples or MIN_SAMPLES, min_support=3, **over), provider


def test_t_m5_09_same_split_same_artifact_even_shuffled() -> None:
    split = _examples("es", 12)
    first, _ = _run(split)
    shuffled = sorted(split, key=lambda e: sha256_hex(e.id.encode()))  # orden barajado pero reproducible
    assert shuffled != split
    second, _ = _run(shuffled)
    assert first.to_json() == second.to_json() and first.run_id == second.run_id
    assert first.split_hash == second.split_hash and len(first.split_hash) == 64


def test_changing_one_example_changes_split_hash_and_run_id() -> None:
    split = _examples("es", 12)
    base, _ = _run(split)
    changed = [replace(split[0], labels={"command": "cancel"}), *split[1:]]
    other, _ = _run(changed)
    assert other.split_hash != base.split_hash and other.run_id != base.run_id
    assert base.run_id.startswith("cal-") and len(base.run_id) == 4 + 16


def _with_config(config: dict[str, JsonValue]) -> DecisionModelDef:
    base = model_def()
    return base.model_copy(update={"providers": [ProviderSpec(provider="classifier", config=config)]})


def test_t_m5_09_run_id_covers_the_provider_config() -> None:
    split = _examples("es", 12)
    a, _ = _run(split, definition=_with_config({"prompt": "criterio A"}))
    b, _ = _run(split, definition=_with_config({"prompt": "criterio B"}))
    same, _ = _run(_examples("es", 12)[::-1], definition=_with_config({"prompt": "criterio A"}))
    assert a.split_hash == b.split_hash and a.run_id != b.run_id
    assert a.run_id == same.run_id and a.to_json() == same.to_json()
    empty, _ = _run(split)
    assert empty.run_id != a.run_id


def test_run_id_is_independent_of_config_key_order() -> None:
    split = _examples("es", 12)
    a, _ = _run(split, definition=_with_config({"x": 1, "y": {"b": 2, "a": 3}}))
    b, _ = _run(split, definition=_with_config({"y": {"a": 3, "b": 2}, "x": 1}))
    assert a.run_id == b.run_id


def test_calibrators_and_thresholds_are_produced_and_usable_by_decide() -> None:
    art, _ = _run(_examples("es", 12))
    assert art.method == "isotonic" and art.target == TARGETS
    cal = art.calibrator("command", "classifier", "es")
    assert cal is not None
    threshold = art.thresholds[("command", "affirm", "classifier", "es")]
    definition = model_def(calibration_run=art.run_id, thresholds_from=art.run_id)
    rig = make_service(definition, artifacts=[art])
    rig.providers["classifier"].push(RawPrediction(value={"command": "affirm"}, p_raw={"command": 0.85}))
    out = rig.service.decide_output(ref(definition), {"text": "x"}, "es", rig.vault)
    assert out.p_cal["command"] == pytest.approx(cal.apply(0.85))
    assert out.above_threshold["command"] is (cal.apply(0.85) >= threshold)


def test_portuguese_below_the_minimum_copies_spanish_and_records_the_limitation() -> None:
    art, _ = _run(_examples("es", 12) + _examples("pt", 4, synthetic=True), n_pt=4)
    assert art.calibrator("command", "classifier", "pt") == art.calibrator("command", "classifier", "es")
    assert (art.threshold("command", "affirm", "classifier", "pt")
            == art.threshold("command", "affirm", "classifier", "es"))
    assert art.limitations == ["pt: calibración copiada de es (muestra < mínimo)"]


def test_portuguese_absent_from_the_split_also_copies_spanish() -> None:
    art, _ = _run(_examples("es", 12))
    assert art.calibrator("command", "classifier", "pt") is not None
    assert art.limitations == ["pt: calibración copiada de es (muestra < mínimo)"]


def test_portuguese_with_enough_samples_keeps_its_own_calibration() -> None:
    art, _ = _run(_examples("es", 12) + _examples("pt", 9, synthetic=True), n_pt=9)
    pt = art.calibrator("command", "classifier", "pt")
    assert pt is not None and pt != art.calibrator("command", "classifier", "es")
    assert art.limitations == []


def test_provider_failures_do_not_abort_and_count_as_missing_predictions() -> None:
    split = _examples("es", 12)
    provider = ScriptedProvider("classifier")
    provider.push(Failure("x"), Failure("y"))
    _script(provider, 10)
    art = calibrate(model_def(), split, {"classifier": provider}, targets=TARGETS, min_samples={"es": 1},
                    min_support=3)
    assert art.calibrator("command", "classifier", "es") is not None


def test_null_p_raw_yields_no_calibrator_and_no_threshold() -> None:
    provider = ScriptedProvider("classifier")
    for _ in range(12):
        provider.push(RawPrediction(value={"command": "affirm"}, p_raw={"command": None}))
    art = calibrate(model_def(), _examples("es", 12), {"classifier": provider}, targets=TARGETS,
                    min_samples={"es": 1}, min_support=3)
    assert art.calibrators == {} and art.thresholds == {}


def test_out_of_schema_prediction_counts_as_missing() -> None:
    provider = ScriptedProvider("classifier")
    for _ in range(12):
        provider.push(RawPrediction(value={"command": "inventado"}, p_raw={"command": 0.9}))
    art = calibrate(model_def(), _examples("es", 12), {"classifier": provider}, targets=TARGETS,
                    min_samples={"es": 1}, min_support=3)
    assert art.calibrators == {} and art.thresholds == {}


def test_method_none_has_no_calibrators_but_still_has_thresholds_on_p_raw() -> None:
    definition = model_def(calibration_method="none", calibration_run=None, thresholds_from=None)
    art, _ = _run(_examples("es", 12), definition=definition)
    assert art.method == "none" and art.calibrators == {}
    assert ("command", "affirm", "classifier", "es") in art.thresholds


def test_unsupported_method_is_a_config_error() -> None:
    with pytest.raises(DecisionConfigError):
        _run(_examples("es", 12), definition=model_def(calibration_method="platt"))


def test_spanish_is_the_base_language_and_required() -> None:
    with pytest.raises(ValueError, match="es"):
        _run(_examples("pt", 9), n_es=0, n_pt=9)


def test_missing_target_or_min_samples_is_an_error() -> None:
    provider = ScriptedProvider("classifier")
    with pytest.raises(ValueError, match="target"):
        calibrate(model_def(), _examples("es", 3), {"classifier": provider}, targets={},
                  min_samples={"es": 1}, min_support=1)
    with pytest.raises(ValueError, match="min_samples"):
        calibrate(model_def(), _examples("es", 3), {"classifier": provider}, targets=TARGETS,
                  min_samples={}, min_support=1)


def test_unregistered_provider_is_a_config_error() -> None:
    with pytest.raises(DecisionConfigError):
        calibrate(model_def(), _examples("es", 3), {}, targets=TARGETS, min_samples={"es": 1}, min_support=1)


def test_inputs_reach_the_provider_as_given_and_scope_helper_is_unused() -> None:
    _, provider = _run(_examples("es", 12))
    assert provider.calls[0][1] == {"text": "texto es 0"} and provider.calls[0][2] == "es"
    assert scope().run_id == "run-1"
