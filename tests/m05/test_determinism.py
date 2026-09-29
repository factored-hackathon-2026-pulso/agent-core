"""Determinismo (regla dura 3): mismos puertos y mismo `Clock` producen los mismos eventos."""

from decimal import Decimal

from agent_core.decision.calibration import Target
from agent_core.decision.types import RawPrediction
from agent_core.decision.understand import UnderstandContext, UnderstandService
from agent_core.domain import MEASURED_FIELDS, JsonValue
from testing.fakes.provider import Reply, Timeout
from tests.m05.helpers import Rig, artifact, identity_map, make_service, model_def, ref, scope

FIELDS = ("command", "flow", "interrupt")


def _strip_measured(event_dump: dict[str, JsonValue]) -> dict[str, JsonValue]:
    payload = dict(event_dump["payload"])  # type: ignore[call-overload]
    for name in MEASURED_FIELDS["decision_made"]:
        payload.pop(name, None)
    return {**event_dump, "payload": payload}


def _world() -> tuple[Rig, UnderstandContext]:
    definition = model_def(providers=("jev", "classifier"), calibrated=FIELDS)
    art = artifact(
        thresholds={("command", "start_flow", "classifier", "es"): 0.5,
                    ("flow", "disputa", "classifier", "es"): 0.5},
        calibrators={(name, "classifier", "es"): identity_map() for name in FIELDS},
        target={"command": Target(metric="precision", value=0.9)})
    rig = make_service(definition, artifacts=[art])
    rig.providers["jev"].push(Timeout(after_ms=50), Timeout(after_ms=50))
    token = rig.vault.tokenize("1234567890", "document_number", "doc")
    for _ in range(2):
        rig.providers["classifier"].push(Reply(RawPrediction(
            value={"command": "start_flow", "flow": "disputa", "slots": {"doc": token}},
            p_raw={"command": 0.9, "flow": 0.8}, tokens=3, cost_usd=Decimal("0.001"),
            model_version="clf-1"), latency_ms=12))
    context = UnderstandContext(
        model_ref=ref(definition), flows=["disputa"], interrupts=["cancelar"], current_node=None,
        confirm_pending=False, recent_turns=["hola"], token_vault=rig.vault, scope=scope())
    return rig, context


def _run_once() -> list[dict[str, JsonValue]]:
    rig, context = _world()
    _, decide_event = rig.service.decide(context.model_ref, {"text": "x"}, "es", rig.vault, scope=scope())
    # la primera decisión consumió un Timeout de jev y una respuesta del clasificador
    _, events = UnderstandService(rig.service).run("quiero disputar", context, "es")
    return [_strip_measured(e.model_dump(mode="json")) for e in [decide_event, *events]]


def test_two_full_runs_produce_equal_events_ignoring_measured_fields() -> None:
    assert _run_once() == _run_once()
    assert len(_run_once()) == 2
