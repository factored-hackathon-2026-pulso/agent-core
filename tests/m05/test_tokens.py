"""T-M5-04: un token desconocido invalida la salida (todos los campos calibrados bajo umbral)."""

from agent_core.decision.types import DecisionOutput, RawPrediction
from agent_core.domain import JsonValue
from tests.m05.helpers import artifact, identity_map, make_service, ref

THRESHOLDS = {("command", "affirm", "classifier", "es"): 0.1}
CALIBRATORS = {("command", "classifier", "es"): identity_map()}


def _run(value: dict[str, JsonValue], *, known_document: bool = True) -> DecisionOutput:
    rig = make_service(artifacts=[artifact(thresholds=THRESHOLDS, calibrators=CALIBRATORS)])
    if known_document:
        assert rig.vault.tokenize("1234567890", "document_number", "doc") == "⟦doc:1⟧"
    rig.providers["classifier"].push(RawPrediction(value=value, p_raw={"command": 0.99}))
    return rig.service.decide_output(ref(), {"text": "x"}, "es", rig.vault)


def test_t_m5_04_known_token_keeps_its_threshold() -> None:
    out = _run({"command": "affirm", "slots": {"documento": "⟦doc:1⟧"}})
    assert out.above_threshold == {"command": True}


def test_t_m5_04_unknown_token_invalidates_everything() -> None:
    out = _run({"command": "affirm", "slots": {"documento": "⟦doc:9⟧"}})
    assert out.above_threshold == {"command": False}
    assert out.p_cal["command"] == 0.99  # se conserva para diagnóstico
    assert out.value["slots"] == {"documento": "⟦doc:9⟧"}  # el valor no se toca


def test_token_inside_long_text_is_detected() -> None:
    out = _run({"command": "affirm", "slots": {"nota": "el documento ⟦doc:9⟧ del cliente"}})
    assert out.above_threshold == {"command": False}


def test_token_inside_nested_list_is_detected() -> None:
    out = _run({"command": "affirm", "slots": {"lista": [["a", {"b": ["⟦doc:9⟧"]}]]}})
    assert out.above_threshold == {"command": False}


def test_token_used_as_a_key_is_detected() -> None:
    out = _run({"command": "affirm", "slots": {"⟦doc:9⟧": "x"}})
    assert out.above_threshold == {"command": False}


def test_token_in_a_top_level_string_field_is_detected() -> None:
    out = _run({"command": "affirm", "flow": "⟦doc:9⟧"})
    assert out.above_threshold == {"command": False}


def test_token_without_any_known_entry_is_unknown() -> None:
    out = _run({"command": "affirm", "slots": {"x": "⟦doc:1⟧"}}, known_document=False)
    assert out.above_threshold == {"command": False}


def test_lookalikes_do_not_trigger() -> None:
    out = _run({"command": "affirm", "slots": {"a": "⟦DOC:1⟧", "b": "⟦doc:0⟧", "c": "[[doc:9]]"}})
    assert out.above_threshold == {"command": True}
