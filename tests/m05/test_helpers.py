from agent_core.decision.schema import check_schema_supported, validate_output
from tests.m05.helpers import artifact, identity_map, model_def, vault


def test_model_def_is_valid_and_schema_is_supported() -> None:
    definition = model_def(providers=("jev", "classifier"))
    assert [p.provider for p in definition.providers] == ["jev", "classifier"]
    check_schema_supported(definition.output_schema)
    assert validate_output({"command": "affirm", "slots": {"x": 1}}, definition.output_schema) == []


def test_artifact_and_identity_map() -> None:
    art = artifact(calibrators={("command", "classifier", "es"): identity_map()})
    assert art.calibrator("command", "classifier", "es") is not None
    assert identity_map().apply(0.37) == 0.37


def test_vault_tokenizes_synthetic_values() -> None:
    assert vault().tokenize("1234567890", "document_number", "doc") == "⟦doc:1⟧"
