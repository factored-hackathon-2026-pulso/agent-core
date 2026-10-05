"""El conjunto sintético de calibración de `understand-turno`: forma, cobertura y separación dev/test.

No llama a JEV ni a nada externo: solo valida que el conjunto sirva para calibrar y medir sin trampa."""

import re

import pytest

from agent_core.decision.calibration import DevExample
from agent_core.domain import Command
from testing.calibration.understand_turno import COMMANDS, FLOWS, examples, summary

ALL = [*examples("dev"), *examples("test")]
MIN_DEV_ES = 25     # por comando: por debajo, `choose_threshold` omite el valor (queda en 1.0)
MIN_TEST_ES = 10    # por comando: para que la medición cerrada diga algo


def _text(e: DevExample) -> str:
    text = e.inputs["text"]
    assert isinstance(text, str)
    return text


def test_commands_are_exactly_the_engine_commands() -> None:
    assert set(COMMANDS) == {c.value for c in Command}


def test_ids_are_unique_across_both_splits() -> None:
    ids = [e.id for e in ALL]
    assert len(ids) == len(set(ids))


def test_the_listing_is_deterministic_and_sorted_by_id() -> None:
    assert examples("dev") == examples("dev")
    assert [e.id for e in examples("test")] == sorted(e.id for e in examples("test"))


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_has_enough_spanish_support(command: str) -> None:
    assert summary("dev")["es"][command] >= MIN_DEV_ES
    assert summary("test")["es"][command] >= MIN_TEST_ES


def test_test_split_shares_no_message_with_dev() -> None:
    """Ni la frase ni su variante sin signos: `test` no puede contener lo que vio `dev`."""
    def plain(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[¿?¡!.,;]", "", text)).strip().lower()

    dev = {plain(_text(e)) for e in examples("dev")}
    leaked = [_text(e) for e in examples("test") if plain(_text(e)) in dev]
    assert leaked == []


def test_spanish_is_real_and_portuguese_is_marked_synthetic() -> None:
    assert all(e.synthetic == (e.lang == "pt") for e in ALL)
    assert {e.lang for e in ALL} == {"es", "pt"}


def test_labels_follow_how_understand_reads_them() -> None:
    for e in ALL:
        command = e.labels["command"]
        assert command in COMMANDS
        assert ("flow" in e.labels) == (command == "start_flow")
        assert ("interrupt" in e.labels) == (command == "interrupt")
        if "flow" in e.labels:
            assert e.labels["flow"] in FLOWS
        if "interrupt" in e.labels:
            assert e.labels["interrupt"] == "fraude"
        assert set(e.labels) <= {"command", "flow", "interrupt"}


def test_context_matches_the_command() -> None:
    for e in ALL:
        node, confirm = e.inputs["current_node"], e.inputs["confirm_pending"]
        command = e.labels["command"]
        if command == "start_flow":
            assert node is None and confirm is False  # el prompt solo permite start_flow sin nodo actual
        if command == "continue":
            assert node is not None and confirm is False
        if command in ("affirm", "deny"):
            assert confirm is True
        if confirm:
            assert node == "confirmar"
        assert (e.inputs["recent_turns"] == []) == (node is None)


def test_inputs_have_exactly_the_keys_understand_sends() -> None:
    for e in ALL:
        assert set(e.inputs) == {"text", "recent_turns", "current_node", "confirm_pending"}
        assert _text(e).strip() and len(_text(e)) < 300


def test_no_personal_data_shapes_in_the_texts() -> None:
    forbidden = [re.compile(r"@"), re.compile(r"\b\d{12,}\b"), re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}\b")]
    for e in ALL:
        assert not any(rx.search(_text(e)) for rx in forbidden), e.id


def test_both_flows_and_both_disputes_and_queries_are_covered_in_each_split() -> None:
    for split in ("dev", "test"):
        flows = {e.labels["flow"] for e in examples(split) if e.lang == "es" and "flow" in e.labels}
        assert flows == set(FLOWS)
