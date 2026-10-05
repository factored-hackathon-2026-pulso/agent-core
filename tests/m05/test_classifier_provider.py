from pathlib import Path

import pytest

from agent_core.decision.providers.classifier import ClassifierProvider
from agent_core.decision.service import DecisionService
from agent_core.decision.types import DecisionConfigError, ProviderError
from agent_core.domain import JsonValue, ProviderSpec, dumps, loads
from testing.fakes.provider import Timeout
from tests.m05.helpers import make_service, model_def, ref

FIXTURE = Path(__file__).parent / "fixtures" / "classifier_tiny.json"
SPEC = ProviderSpec(provider="classifier", config={"artifact": "clf-tiny"})


class DictLoader:
    def __init__(self, artifacts: dict[str, str]) -> None:
        self.artifacts = artifacts
        self.loads = 0

    def load(self, ref: str) -> str:
        self.loads += 1
        return self.artifacts[ref]


def _provider(text: str | None = None) -> ClassifierProvider:
    return ClassifierProvider(DictLoader({"clf-tiny": text or FIXTURE.read_text(encoding="utf-8")}))


def _predict(provider: ClassifierProvider, text: str, extra: dict[str, JsonValue] | None = None):  # type: ignore[no-untyped-def]
    return provider.predict(SPEC, {"text": text, **(extra or {})}, {}, "es")


@pytest.mark.parametrize(("text", "expected"), [
    ("Sí, claro", "affirm"),
    ("sim, pode ser", "affirm"),
    ("não, obrigado", "deny"),
    ("no quiero", "deny"),
    ("cancela por favor", "cancel"),
])
def test_predicts_the_expected_class(text: str, expected: str) -> None:
    raw = _predict(_provider(), text)
    assert raw.value == {"command": expected}
    assert raw.p_raw["command"] is not None and raw.p_raw["command"] > 0.5
    assert raw.model_version == "classifier:0123456789ab"


def test_top_k_is_sorted_and_sums_to_at_most_one() -> None:
    raw = _predict(_provider(), "sí")
    top = raw.top_k["command"]
    assert top[0][0] == "affirm"
    assert [p for _, p in top] == sorted((p for _, p in top), reverse=True)
    assert sum(p for _, p in top) == pytest.approx(1.0) and sum(p for _, p in top) <= 1.0 + 1e-12
    assert raw.p_raw["command"] == top[0][1]


def test_ties_break_by_label_for_stability() -> None:
    raw = _predict(_provider(), "texto sin palabras conocidas")  # x = 0: tres clases empatadas
    assert [label for label, _ in raw.top_k["command"]] == ["affirm", "cancel", "deny"]
    assert raw.value == {"command": "affirm"}


def test_same_input_gives_exactly_the_same_output() -> None:
    provider = _provider()
    first, second = _predict(provider, "sí, por favor no"), _predict(provider, "sí, por favor no")
    assert first == second


def test_extra_input_keys_are_ignored_but_text_is_required() -> None:
    provider = _provider()
    raw = _predict(provider, "sí", {"recent_turns": ["hola"], "confirm_pending": False})
    assert raw.value == {"command": "affirm"}
    with pytest.raises(ProviderError):
        provider.predict(SPEC, {"recent_turns": []}, {}, "es")
    with pytest.raises(ProviderError):
        provider.predict(SPEC, {"text": 3}, {}, "es")


def test_artifact_is_loaded_once_per_ref() -> None:
    loader = DictLoader({"clf-tiny": FIXTURE.read_text(encoding="utf-8")})
    provider = ClassifierProvider(loader)
    _predict(provider, "sí")
    _predict(provider, "no")
    assert loader.loads == 1


def test_unknown_format_is_a_config_error() -> None:
    doc = loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    with pytest.raises(DecisionConfigError):
        _predict(_provider(dumps({**doc, "format": "otro"})), "sí")


@pytest.mark.parametrize("mutation", [
    {"idf": [1.0]},                                     # idf no cubre el vocabulario
    {"coef": {"command": [[1.0]]}},                     # filas de largo distinto
    {"intercept": {"command": [0.0]}},                  # intercepto de largo distinto
    {"classes": {"command": []}},
    {"vocab": {"sí": 9}},                               # índice fuera de rango
])
def test_inconsistent_artifact_is_a_config_error(mutation: dict[str, JsonValue]) -> None:
    doc = loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    with pytest.raises(DecisionConfigError):
        _predict(_provider(dumps({**doc, **mutation})), "sí")


def test_missing_artifact_config_or_unloadable_ref_is_a_config_error() -> None:
    provider = _provider()
    with pytest.raises(DecisionConfigError):
        provider.predict(ProviderSpec(provider="classifier"), {"text": "sí"}, {}, "es")
    with pytest.raises(DecisionConfigError):
        provider.predict(ProviderSpec(provider="classifier", config={"artifact": "no-existe"}),
                         {"text": "sí"}, {}, "es")


def test_classifier_inside_the_service_after_a_timeout() -> None:
    definition = model_def(providers=("jev", "classifier"), calibration_method="none", calibration_run=None,
                           thresholds_from=None)
    definition = definition.model_copy(update={"providers": [definition.providers[0], SPEC]})
    rig = make_service(definition)
    rig.providers["jev"].push(Timeout())
    service = DecisionService(rig.registry, {"jev": rig.providers["jev"], "classifier": _provider()},
                              rig.sources, rig.clock, rig.ids)
    out = service.decide_output(ref(definition), {"text": "cancela"}, "es", rig.vault)
    assert out.provider_used == "classifier" and out.fallback_depth == 1
    assert out.value == {"command": "cancel"} and out.p_cal["command"] == out.p_raw["command"]


# --- `text_from`: el texto sale de otra clave de la entrada (el `input_view` no se llama `text`) --


def _predict_from(config: dict[str, JsonValue], inputs: dict[str, JsonValue]):  # type: ignore[no-untyped-def]
    spec = ProviderSpec(provider="classifier", config={"artifact": "clf-tiny", **config})
    return _provider().predict(spec, inputs, {}, "es")


def test_text_from_reads_the_named_input_key() -> None:
    raw = _predict_from({"text_from": "slots.respuesta"}, {"slots.respuesta": "no quiero"})
    assert raw.value == {"command": "deny"}


def test_text_from_accepts_a_wrapped_untrusted_value() -> None:
    wrapped = '<datos_no_confiables fuente="slot">cancela por favor</datos_no_confiables>'
    assert _predict_from({"text_from": "slots.x"}, {"slots.x": wrapped}).value == {"command": "cancel"}


def test_text_from_a_list_joins_the_texts_in_order() -> None:
    raw = _predict_from({"text_from": ["a", "b"]}, {"a": "sí", "b": "claro"})
    assert raw.value == {"command": "affirm"}


def test_text_from_missing_key_is_a_provider_error() -> None:
    with pytest.raises(ProviderError):
        _predict_from({"text_from": "slots.x"}, {"text": "sí"})


@pytest.mark.parametrize("bad", [1, [], [1], {"a": 1}, ""])
def test_malformed_text_from_is_a_config_error(bad: JsonValue) -> None:
    with pytest.raises(DecisionConfigError):
        _predict_from({"text_from": bad}, {"text": "sí"})
