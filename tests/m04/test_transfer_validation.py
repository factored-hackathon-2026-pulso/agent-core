"""M4 validates a transfer and follows `rejected` when it fails (ADR 0021, spec §5.2)."""

from typing import Any

from agent_core.audit import AuditLog
from agent_core.domain import (
    AgentSelector,
    DirectorySnapshot,
    EntityKind,
    Principal,
    RunInput,
    SubjectRef,
    to_jsonable,
)
from agent_core.interpreter import TransferRequest
from agent_core.ports import AuthzDecision
from agent_core.turn import TurnEngine
from agent_core.turn.transfer import event_target
from agent_core.views import verify_fingerprint
from testing.fakes.authz import TableAuthz
from testing.fakes.keys import FakeKeyProvider
from tests.m04.harness import DEFAULT_ACCEPTS, RUN_ID, SPECIALIST_RELEASE_ID, FakeRuntimeFactory, World
from tests.m04.helpers import MODEL_MARK, cmd

TEXT = "no reconozco un cargo"


def _rejected(w: World) -> str:
    rejected = [e for e in w.events() if e.type == "transfer_rejected"]
    assert len(rejected) == 1
    return str(rejected[0].payload.reason_code)


def _turn(w: World) -> Any:
    w.understand.push(cmd("continue"))
    return w.turn(TEXT)


def test_target_not_in_the_directory_read_is_rejected() -> None:
    w = World()
    w.reception(choice="saldos")  # published under `prod`, but absent from the snapshot the run read
    result = _turn(w)
    assert _rejected(w) == "not_in_directory"
    assert result.status == "escalated"  # `rejected` → `esc`
    assert w.saved().active_flow is not None and w.saved().active_flow.node_id == "esc"


def test_rejection_follows_the_rejected_branch_after_the_event() -> None:
    w = World()
    w.reception(choice="saldos")
    _turn(w)
    types = w.event_types()
    at = types.index("transfer_rejected")
    assert types[at - 1] == "node_entered"  # the `transfer` node
    assert "escalated" in types[at:] and types[-2:] == ["run_closed", "turn_completed"]
    closed = [e for e in w.events() if e.type == "run_closed"]
    assert len(closed) == 1 and closed[0].payload.closed_by == "escalation"


def test_rejected_payload_names_the_directory_read() -> None:
    w = World()
    state = w.reception(choice="saldos")
    assert state is not None
    _turn(w)
    event = next(e for e in w.events() if e.type == "transfer_rejected")
    snapshot = state.facts["directorio"].value
    assert isinstance(snapshot, dict)
    assert event.payload.to_agent is None  # only a target of the directory read is echoed
    assert event.payload.directory == "customer-care" and event.payload.directory_hash == snapshot["hash"]
    assert event.payload.transfer_id.startswith("transfer-")


def test_none_choice_is_not_in_directory() -> None:
    w = World()
    w.reception(choice="none")
    _turn(w)
    assert _rejected(w) == "not_in_directory"


def test_revoked_specialist_is_no_active_release() -> None:
    w = World()
    w.reception()
    w.registry.revoke(w.specialist_release_id)
    result = _turn(w)
    assert _rejected(w) == "no_active_release"
    assert result.status == "escalated"


def test_ineligible_principal_is_rejected() -> None:
    w = World()
    w.reception(specialist_over={"invocable_by": ["advisor"]})
    _turn(w)
    assert _rejected(w) == "not_eligible"


def test_specialist_without_contract_is_not_eligible() -> None:
    w = World()
    w.reception(specialist_over={"accepts": None})
    _turn(w)
    assert _rejected(w) == "not_eligible"


class _DenySubject(TableAuthz):
    def authorize_subject(self, principal: Principal, obo: Any, subject: SubjectRef | None) -> AuthzDecision:
        return AuthzDecision(allowed=False, reason="synthetic")


class _DenyAgent(TableAuthz):
    def authorize_agent(self, principal: Principal, agent: Any, subject: SubjectRef | None) -> AuthzDecision:
        return AuthzDecision(allowed=False, reason="synthetic")


def test_authz_subject_denial_is_not_eligible() -> None:
    w = World()
    w.engine = TurnEngine(**{**w.engine_kwargs(), "authz": _DenySubject()})
    w.reception()
    _turn(w)
    assert _rejected(w) == "not_eligible"


def test_authz_agent_denial_is_not_eligible() -> None:
    w = World()
    w.engine = TurnEngine(**{**w.engine_kwargs(), "authz": _DenyAgent()})
    w.reception()
    _turn(w)
    assert _rejected(w) == "not_eligible"


def test_packet_outside_the_contract_is_rejected() -> None:
    w = World()
    w.reception(accepts={"slots": {"tarjeta": {"type": "string", "required": True}}})
    result = _turn(w)
    assert _rejected(w) == "accepts_mismatch"
    assert result.status == "escalated"


def test_transfer_limit_is_enforced_by_depth() -> None:
    w = World()
    w.reception(origin_depth=1)  # this run already came from a transfer
    _turn(w)
    assert _rejected(w) == "transfer_limit"


def test_validation_order_follows_the_spec() -> None:
    """§5.2: directory, release, eligibility, contract and, last, the session limit."""
    w = World()
    w.reception(choice="saldos", origin_depth=1)
    _turn(w)
    assert _rejected(w) == "not_in_directory"
    w = World()
    w.reception(specialist_over={"invocable_by": ["advisor"]}, origin_depth=1)
    w.registry.revoke(w.specialist_release_id)
    _turn(w)
    assert _rejected(w) == "no_active_release"
    w = World()
    w.reception(specialist_over={"invocable_by": ["advisor"]},
                accepts={"slots": {"tarjeta": {"type": "string", "required": True}}}, origin_depth=1)
    _turn(w)
    assert _rejected(w) == "not_eligible"
    w = World()
    w.reception(accepts={"slots": {"tarjeta": {"type": "string", "required": True}}}, origin_depth=1)
    _turn(w)
    assert _rejected(w) == "accepts_mismatch"


def test_transfer_during_start_run_is_no_turn() -> None:
    w = World()
    w.reception(open_run=False)
    run_input = RunInput.model_validate(
        {"agent": AgentSelector(id="recepcion-directa", alias="prod"), "idempotency_key": "key-1",
         "subject": {"kind": "customer", "ref": "cust-001"}})
    result = w.engine.start_run(w.principal, None, run_input)
    events = w.store.events[result.run_id]
    rejected = [e for e in events if e.type == "transfer_rejected"]  # type: ignore[attr-defined]
    assert [e.payload.reason_code for e in rejected] == ["no_turn"]  # type: ignore[attr-defined]
    assert result.status == "escalated"


def test_rejected_events_carry_no_slot_values() -> None:
    w = World()
    w.reception(choice="saldos")
    _turn(w)
    dumped = "".join(e.model_dump_json() for e in w.events())
    assert "no reconozco" not in dumped


def test_valid_transfer_emits_run_transferred_with_a_keyed_packet_fingerprint() -> None:
    w = World(chain_factory=AuditLog)  # the target's origin links to the hash of the origin chain
    w.reception()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    w.turn(TEXT)
    events = w.events()
    assert "transfer_rejected" not in [e.type for e in events]
    transferred = [e for e in events if e.type == "run_transferred"]
    assert len(transferred) == 1
    payload = transferred[0].payload
    assert payload.to_agent.id == "disputas" and payload.to_release_id == w.specialist_release_id
    assert payload.reason == "routed" and payload.candidates == ["disputas"]
    assert payload.directory == "customer-care"
    packet = {"reason": "routed", "trigger": f"{MODEL_MARK}{TEXT}", "slots": {"problema": TEXT}}
    assert payload.packet_fp.alg == "HMAC-SHA256"
    assert verify_fingerprint(to_jsonable(packet), payload.packet_fp, FakeKeyProvider.default())
    closed = [e for e in events if e.type == "run_closed"]
    assert len(closed) == 1 and closed[0].payload.closed_by == "transfer"
    assert closed[0].payload.outcome == "transferred"
    dumped = "".join(e.model_dump_json() for e in events)
    assert "no reconozco" not in dumped  # T-TR-15: only the fingerprint travels
    saved = w.store.runs[RUN_ID]
    assert saved.status == "closed" and saved.active_flow is not None
    assert saved.active_flow.node_id == "transferir"  # the lineage shows where it happened


# --- review fixes (2026-10-01) ---------------------------------------------------------------------------

STRICT = {"slots": {"tarjeta": {"type": "string", "required": True}}}
# Synthetic PII-like data only: a test card number (Luhn-valid test range) and an invented name.
PII_NAME = "Ana Sintética Prueba"
PII_CARD = "4111 1111 1111 1111"
PII_TEXT = f"soy {PII_NAME}, no reconozco un cargo en la tarjeta {PII_CARD}"


def test_target_in_the_directory_is_echoed_in_the_rejection() -> None:
    w = World()
    w.reception()
    w.registry.revoke(w.specialist_release_id)
    _turn(w)
    event = next(e for e in w.events() if e.type == "transfer_rejected")
    assert event.payload.to_agent == "disputas"


def test_contract_comes_from_the_release_resolved_now_not_from_the_snapshot() -> None:
    w = World()
    w.reception(accepts=STRICT, snapshot_accepts=DEFAULT_ACCEPTS)  # the directory read showed a looser one
    _turn(w)
    assert _rejected(w) == "accepts_mismatch"


def test_missing_prod_alias_is_no_active_release() -> None:
    w = World()
    w.reception(publish_specialist=False)  # listed in the directory read, but no `prod` alias resolves
    result = _turn(w)
    assert _rejected(w) == "no_active_release"
    assert result.status == "escalated"


def test_release_pinning_an_unknown_agent_version_is_no_active_release() -> None:
    w = World()
    w.reception()
    current = w.registry.resolve_release_by_id(SPECIALIST_RELEASE_ID)
    agents = {**current.entities[EntityKind.agent], "disputas": "9.9.9"}
    entities = {**current.entities, EntityKind.agent: agents}
    moved = current.model_copy(update={"id": "rel-disputas-2", "entities": entities})
    w.registry.add_release(moved, "disputas")  # `prod` moves to a release whose agent version is missing
    result = _turn(w)
    assert _rejected(w) == "no_active_release"
    assert result.status == "escalated"


def _request(target: str | None, snapshot: DirectorySnapshot | None) -> TransferRequest:
    return TransferRequest(node_id="transferir", target=target, snapshot=snapshot, reason="routed")


def test_event_target_only_echoes_ids_of_the_directory_read() -> None:
    w = World()
    state = w.reception()
    assert state is not None
    value = state.facts["directorio"].value
    assert isinstance(value, dict)
    snapshot = DirectorySnapshot.model_validate({k: v for k, v in value.items() if k != "choices"})
    assert event_target(_request("disputas", snapshot)) == "disputas"
    assert event_target(_request("No es un id! 4111", snapshot)) is None  # malformed
    assert event_target(_request("saldos", snapshot)) is None  # well formed, but not in the directory read
    assert event_target(_request(None, snapshot)) is None
    assert event_target(_request("disputas", None)) is None


def _capture_runtimes(w: World) -> list[Any]:
    opened: list[Any] = []
    original = FakeRuntimeFactory.open

    def open_and_keep(self: FakeRuntimeFactory, *args: Any) -> Any:
        runtime = original(self, *args)
        opened.append(runtime)
        return runtime

    w.runtimes.open = open_and_keep.__get__(w.runtimes)  # type: ignore[method-assign]
    return opened


def _event_dumps(w: World) -> str:
    """Every event of the session: the origin run and, after a transfer, the target run."""
    runs = [r.run_id for r in w.session_runs()] or [RUN_ID]
    return "\n".join(e.model_dump_json() for run_id in runs for e in w.audit.read(run_id))


def _assert_no_pii(dumped: str) -> None:
    for needle in (PII_NAME, PII_CARD, PII_CARD.replace(" ", ""), "no reconozco"):
        assert needle not in dumped


def test_valid_transfer_events_carry_no_pii_of_the_packet_nor_of_the_vault() -> None:
    """T-TR-15, non-vacuous: the PII is both the trigger and the slot that travels in the packet."""
    w = World(chain_factory=AuditLog)
    opened = _capture_runtimes(w)
    w.reception()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    w.turn(PII_TEXT)
    assert len(w.session_runs()) == 2  # the target run's events are checked too
    transferred = next(e for e in w.events() if e.type == "run_transferred")
    packet = {"reason": "routed", "trigger": f"{MODEL_MARK}{PII_TEXT}", "slots": {"problema": PII_TEXT}}
    assert verify_fingerprint(to_jsonable(packet), transferred.payload.packet_fp, FakeKeyProvider.default())
    dumped = _event_dumps(w)
    _assert_no_pii(dumped)
    vault = opened[-2].step.vault  # the origin's runtime (the last one opened is the target's)
    # Test-only peek at the origin vault. `reason` is a flow literal (it travels in `run_transferred` on
    # purpose); every other vault value comes from the client (trigger and slots) and must not appear.
    values = [entry.value for entry in vault._by_token.values() if entry.value != "routed"]
    assert values  # the packet leaves were tokenized into the origin vault (known side effect)
    for value in values:
        assert value not in dumped


def test_rejected_transfer_events_carry_no_pii() -> None:
    for over in ({"choice": "saldos"}, {"accepts": STRICT}, {"origin_depth": 1}):
        w = World()
        w.reception(**over)  # type: ignore[arg-type]
        w.understand.push(cmd("continue"))
        w.turn(PII_TEXT)
        assert [e.type for e in w.events()].count("transfer_rejected") == 1
        _assert_no_pii(_event_dumps(w))
