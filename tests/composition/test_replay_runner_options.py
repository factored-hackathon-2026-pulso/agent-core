"""`RecordedEngineRunner` y `Driver` reproducen un run con las piezas con que se grabó: sujeto,
`client_turn_id`, catálogo de campos y claves (lo que un run real aporta y el arnés fijaba)."""


import pytest

from agent_core.adapters.system_clock import SystemClock
from agent_core.audit import Replayer
from agent_core.ports import KeyPurpose
from agent_core.views import FieldClassifier, FieldRule
from testing.engine_world import CATALOG, REGISTRY_DEMO, EngineWorld
from testing.fakes.keys import FakeKeyProvider, synthetic_key
from testing.replay import SCENARIOS, build_engine_runner, record_scenario
from testing.replay.scenarios import TEXTO

SUBJECT = {"kind": "customer", "ref": "cust-9"}


def _with_ids(w: EngineWorld) -> None:
    w.start(subject=SUBJECT)
    w.understands("continue")
    w.matches()
    w.turn(TEXTO, client_turn_id="ct-uuid-1")
    w.confirm("no", client_turn_id="ct-uuid-2")


def _replay(fixture, **options):  # type: ignore[no-untyped-def]
    engine = build_engine_runner(REGISTRY_DEMO, **options)
    return Replayer(engine, SystemClock(), definitions=engine.definitions).replay(fixture, "fixture")


def test_the_driver_applies_the_subject_and_the_client_turn_id_of_the_op() -> None:
    w = EngineWorld()
    w.start(subject=SUBJECT)
    w.understands("continue")
    w.matches()
    w.turn(TEXTO, client_turn_id="ct-uuid-1")

    events = w.audit.read(w.driver.run_id)
    assert next(e for e in events if e.type == "run_started").payload.subject_kind == "customer"
    assert [e.payload.client_turn_id for e in events if e.type == "turn_started"][-1] == "ct-uuid-1"


def test_replay_takes_subject_and_client_turn_ids_from_the_recorded_events(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(SCENARIOS, "con_ids", _with_ids)
    fixture = record_scenario("con_ids", REGISTRY_DEMO)
    # Las operaciones que se reconstruyen de un almacén de auditoría no traen ninguno de los dos.
    bare = [{k: v for k, v in op.items() if k not in ("subject", "client_turn_id")} for op in fixture.inputs]

    report = _replay(fixture.model_copy(update={"inputs": bare}))

    assert report.verdict == "match", report.first_divergence


def test_replay_uses_the_key_provider_it_is_given() -> None:
    fixture = record_scenario("cancelado", REGISTRY_DEMO)
    other = FakeKeyProvider(
        keys={KeyPurpose.fingerprint: {"otra-1": synthetic_key("otra-1")},
              KeyPurpose.token_map: {"tm-1": synthetic_key("tm-1")}},
        current={KeyPurpose.fingerprint: "otra-1", KeyPurpose.token_map: "tm-1"})

    assert _replay(fixture).verdict == "match"
    assert _replay(fixture, keys=other).verdict == "diverged"


def test_replay_uses_the_field_catalog_it_is_given() -> None:
    fixture = record_scenario("resuelto", REGISTRY_DEMO)
    stricter = FieldClassifier({**CATALOG, "amount": FieldRule(field_class="pii_direct")})

    assert _replay(fixture).verdict == "match"
    assert _replay(fixture, field_classifier=stricter).verdict == "diverged"
