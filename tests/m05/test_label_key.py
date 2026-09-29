"""La clave de umbral de un valor objeto es la misma en `calibrate` y en `decide` (JCS)."""

from agent_core.decision.calibration.artifact import Target
from agent_core.decision.calibration.calibrate import DevExample, calibrate
from agent_core.decision.types import RawPrediction
from agent_core.domain import JsonValue, canonical_bytes
from testing.fakes.provider import ScriptedProvider
from tests.m05.helpers import make_service, model_def, ref

SCHEMA: dict[str, JsonValue] = {
    "type": "object", "additionalProperties": False, "required": ["target"],
    "properties": {"target": {"type": "object", "additionalProperties": True}},
}


def test_object_valued_field_matches_regardless_of_key_order() -> None:
    definition = model_def(calibrated=("target",))
    definition = definition.model_copy(update={"output_schema": SCHEMA})
    truth = canonical_bytes({"a": 1, "b": 2}).decode("utf-8")
    examples = [DevExample(id=f"es-{i}", inputs={"text": "x"}, labels={"target": truth}, lang="es")
                for i in range(4)]
    provider = ScriptedProvider("classifier")
    for i in range(4):
        provider.push(RawPrediction(value={"target": {"b": 2, "a": 1}}, p_raw={"target": 0.5 + 0.1 * i}))
    art = calibrate(definition, examples, {"classifier": provider},
                    targets={"target": Target(metric="precision", value=0.9)},
                    min_samples={"es": 1}, min_support=1)
    assert art.thresholds  # el valor objeto se etiquetó y se le calculó umbral

    definition = definition.model_copy(update={"calibration": definition.calibration.model_copy(
        update={"run": art.run_id}), "thresholds_from": art.run_id})
    rig = make_service(definition, artifacts=[art])
    rig.providers["classifier"].push(RawPrediction(value={"target": {"b": 2, "a": 1}},
                                                   p_raw={"target": 0.95}))
    out = rig.service.decide_output(ref(definition), {"text": "x"}, "es", rig.vault)
    assert out.above_threshold == {"target": True}
