"""El artefacto sintético de `scripts/serve_state.py` rutea los mensajes típicos y no inventa sin señal."""

import pytest

from agent_core.decision import ClassifierProvider
from agent_core.domain import ProviderSpec
from testing.serve_classifier import synthetic_classifier_json

_THRESHOLD = 0.8  # el de `cal-transfer-demo` para `choice` con el classifier en español

_SPEC = ProviderSpec(provider="classifier", config={"artifact": "sintetico", "text_from": "slots.problema"})


class _Loader:
    def load(self, ref: str) -> str:
        return synthetic_classifier_json()


def _predict(text: str) -> tuple[str, float]:
    result = ClassifierProvider(_Loader()).predict(_SPEC, {"slots.problema": text}, {}, "es")
    return str(result.value["choice"]), float(result.p_raw["choice"] or 0.0)


@pytest.mark.parametrize(("text", "expected"), [
    ("Quiero saber qué productos tengo y sus saldos", "consultas"),
    ("Quiero saber cómo va mi reclamo", "consultas"),
    ("Quiero saber el estado de mi caso", "consultas"),
    ("Quero saber o status da minha solicitação", "consultas"),
    ("Me cobraron dos veces la misma compra", "disputas"),
    ("Quiero una disputa por un cargo duplicado en mi tarjeta", "disputas"),
    ("Me cobraram duas vezes a mesma compra", "disputas"),
])
def test_a_typical_message_is_routed_above_the_demo_threshold(text: str, expected: str) -> None:
    choice, p = _predict(text)
    assert choice == expected
    assert p >= _THRESHOLD


@pytest.mark.parametrize("text", ["Hola buenas tardes", "Necesito ayuda con algo"])
def test_a_message_without_signal_stays_below_the_threshold(text: str) -> None:
    assert _predict(text)[1] < _THRESHOLD
