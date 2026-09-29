"""T-M5-02 y T-M5-03: cadena de proveedores, fallback y reintento por esquema."""

from decimal import Decimal

import pytest

from agent_core.decision.service import DecisionService
from agent_core.decision.types import DecisionConfigError, RawPrediction
from agent_core.domain import JsonValue
from testing.fakes.provider import Failure, Reply, Timeout
from tests.m05.helpers import make_service, model_def, ref

INPUT: dict[str, JsonValue] = {"text": "sí, adelante"}


def _ok(command: str = "affirm", **over: object) -> RawPrediction:
    return RawPrediction(value={"command": command}, p_raw={"command": 0.9}, **over)  # type: ignore[arg-type]


def _bad() -> RawPrediction:
    return RawPrediction(value={"command": "nunca-visto"}, p_raw={"command": 0.9})


def _decide(rig, definition=None, inputs=INPUT):  # type: ignore[no-untyped-def]
    return rig.service.decide_output(ref(definition), inputs, "es", rig.vault)


def test_t_m5_02_timeout_of_first_provider_uses_second() -> None:
    definition = model_def(providers=("jev", "classifier"))
    rig = make_service(definition)
    rig.providers["jev"].push(Timeout(after_ms=500))
    rig.providers["classifier"].push(_ok())
    out = _decide(rig, definition)
    assert out.provider_used == "classifier" and out.fallback_depth == 1 and out.model_calls == 2
    assert out.value == {"command": "affirm"}


def test_provider_error_also_falls_back() -> None:
    definition = model_def(providers=("jev", "classifier"))
    rig = make_service(definition)
    rig.providers["jev"].push(Failure("caído"))
    rig.providers["classifier"].push(_ok())
    out = _decide(rig, definition)
    assert out.provider_used == "classifier" and out.fallback_depth == 1


def test_t_m5_03_out_of_schema_twice_then_next_provider() -> None:
    definition = model_def(providers=("jev", "classifier"))
    rig = make_service(definition)
    rig.providers["jev"].push(_bad(), _bad())
    rig.providers["classifier"].push(_ok())
    out = _decide(rig, definition)
    assert len(rig.providers["jev"].calls) == 2  # 1 reintento con el mismo proveedor
    assert out.provider_used == "classifier" and out.fallback_depth == 1 and out.model_calls == 3


def test_out_of_schema_once_then_retry_succeeds_without_fallback() -> None:
    definition = model_def(providers=("jev", "classifier"))
    rig = make_service(definition)
    rig.providers["jev"].push(_bad(), _ok())
    out = _decide(rig, definition)
    assert out.provider_used == "jev" and out.fallback_depth == 0 and out.model_calls == 2
    assert rig.providers["classifier"].calls == []


def test_exhausted_chain_is_all_below_threshold() -> None:
    definition = model_def(providers=("jev", "classifier"))
    rig = make_service(definition)
    rig.providers["jev"].push(Timeout())
    rig.providers["classifier"].push(Failure("x"))
    out = _decide(rig, definition)
    assert out.value == {} and out.above_threshold == {"command": False}
    assert out.p_cal == {"command": None} and out.p_raw == {"command": None}
    assert out.provider_used == "none" and out.model_version == "none"
    assert out.model_calls == 2 and out.fallback_depth == 2


def test_input_paths_outside_input_view_are_a_config_error() -> None:
    definition = model_def(input_view=("text",))
    rig = make_service(definition)
    rig.providers["classifier"].push(_ok())
    with pytest.raises(DecisionConfigError):
        _decide(rig, definition, {"text": "hola", "documento": "⟦doc:1⟧"})
    assert rig.providers["classifier"].calls == []


def test_empty_input_view_accepts_any_keys() -> None:
    rig = make_service()
    rig.providers["classifier"].push(_ok())
    assert _decide(rig, None, {"cualquiera": "x"}).provider_used == "classifier"


def test_unregistered_provider_is_a_config_error() -> None:
    definition = model_def(providers=("jev", "classifier"))
    rig = make_service(definition)
    service = DecisionService(rig.registry, {"classifier": rig.providers["classifier"]}, rig.sources,
                              rig.clock, rig.ids)
    with pytest.raises(DecisionConfigError):
        service.decide_output(ref(definition), INPUT, "es", rig.vault)


def test_provider_sees_exactly_the_model_view() -> None:
    rig = make_service()
    rig.providers["classifier"].push(_ok())
    _decide(rig)
    assert rig.providers["classifier"].calls[0][1] == INPUT


def test_latency_comes_from_the_clock() -> None:
    rig = make_service()
    rig.providers["classifier"].push(Reply(_ok(), latency_ms=40))
    assert _decide(rig).latency_ms == 40


def test_latency_includes_failed_attempts() -> None:
    definition = model_def(providers=("jev", "classifier"))
    rig = make_service(definition)
    rig.providers["jev"].push(Timeout(after_ms=300))
    rig.providers["classifier"].push(Reply(_ok(), latency_ms=40))
    assert _decide(rig, definition).latency_ms == 340


def test_tokens_and_cost_accumulate_over_all_calls_including_failed() -> None:
    definition = model_def(providers=("jev", "classifier"))
    rig = make_service(definition)
    rig.providers["jev"].push(_ok("nunca-visto", tokens=10, cost_usd=Decimal("0.01")),
                              Failure("x"))
    rig.providers["classifier"].push(_ok(tokens=5, cost_usd=Decimal("0.002")))
    out = _decide(rig, definition)
    assert out.tokens == 15 and out.cost_usd == Decimal("0.012") and isinstance(out.cost_usd, Decimal)


def test_decision_id_comes_from_ids_and_model_version_from_provider() -> None:
    rig = make_service()
    rig.providers["classifier"].push(_ok(model_version="clf-7"))
    out = _decide(rig)
    assert out.decision_id == "decision-0001" and out.model_version == "clf-7"


def test_p_raw_and_top_k_only_for_calibrated_fields() -> None:
    rig = make_service()
    raw = RawPrediction(value={"command": "affirm", "slots": {}}, p_raw={"command": 0.9, "slots": 0.5},
                        top_k={"command": [("affirm", 0.9)], "slots": [("x", 0.1)]})
    rig.providers["classifier"].push(raw)
    out = _decide(rig)
    assert set(out.p_raw) == {"command"} and set(out.top_k) == {"command"}
