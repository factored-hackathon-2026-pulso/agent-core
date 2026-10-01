"""Transfer between agents in M4 (ADR 0021, spec §5.2): validation, packet and the plan the engine runs.

The packet holds `full` values: it never goes into an event. Events carry its keyed fingerprint (`packet_fp`,
HMAC with the M7 key, ADR 0008) and slot names only."""

from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from agent_core.domain import (
    Agent,
    AgentSelector,
    DirectorySnapshot,
    EntityId,
    EntityKind,
    EntityRef,
    Fingerprint,
    Release,
    TransferPacket,
    TransferRejectReason,
    packet_problem,
    to_jsonable,
    transfer_ineligibility,
)
from agent_core.interpreter import TransferRequest
from agent_core.ports import AuthzPort, IdKind, IdSource, RegistryPort
from agent_core.turn.frame import TurnFrame

_ENTITY_ID: TypeAdapter[str] = TypeAdapter(EntityId)
_PACKET_SOURCE = "transfer"
_UNTRUSTED = ["trigger"]  # D7: the message that triggered the transfer is `untrusted_text`


@dataclass(frozen=True)
class TransferPlan:
    """A transfer that passed validation: everything Task 9 needs to open the target run."""

    transfer_id: str
    target: Agent
    target_ref: EntityRef
    release: Release
    to_run_id: str
    packet: TransferPacket
    packet_fp: Fingerprint
    snapshot: DirectorySnapshot
    depth: int


def event_target(request: TransferRequest) -> str | None:
    """The target as `transfer_rejected.to_agent`: echoed only when it is one of the agent ids of the
    directory this run read (and well formed); anything else, e.g. free text from a decision, is `None`."""
    target, snapshot = request.target, request.snapshot
    if target is None or snapshot is None or target not in snapshot.choices:
        return None
    try:
        return _ENTITY_ID.validate_python(target)
    except ValidationError:
        return None


class Transferer:
    def __init__(
        self, registry: RegistryPort, ids: IdSource, authz: AuthzPort | None, max_per_session: int
    ) -> None:
        self._registry = registry
        self._ids = ids
        self._authz = authz
        self._max = max_per_session

    def new_transfer_id(self) -> str:
        return self._ids.new_id(IdKind.transfer)

    def validate(
        self, frame: TurnFrame, request: TransferRequest, transfer_id: str
    ) -> TransferPlan | TransferRejectReason:
        """Spec §5.2, in this order: directory, active release, eligibility, contract, session limit.

        Before them, P6: a transfer reached during `start_run` has no client text to continue with."""
        state = frame.state
        if frame.entry == "start_run" or frame.turn is None:
            return "no_turn"
        snapshot = request.snapshot
        if snapshot is None or request.target is None or request.target not in snapshot.choices:
            return "not_in_directory"  # the model cannot invent a target, even a published one
        release = self._active_release(request.target, frame)
        if release is None:
            return "no_active_release"
        version = release.entities.get(EntityKind.agent, {}).get(request.target)
        if version is None:
            return "no_active_release"
        target_ref = EntityRef(id=request.target, version=version)
        try:
            target = self._registry.get(target_ref, Agent)
        except (KeyError, TypeError):  # the release pins an agent the registry cannot serve
            return "no_active_release"
        if not self._eligible(frame, target):
            return "not_eligible"
        assert target.accepts is not None  # `transfer_ineligibility` rejects an agent without a contract
        if packet_problem(target.accepts, request.slots) is not None:  # contract of the release resolved now
            return "accepts_mismatch"
        depth = (state.origin.depth if state.origin is not None else 0) + 1
        if depth > self._max:
            return "transfer_limit"
        packet = TransferPacket(reason=request.reason, trigger=frame.text_model, slots=dict(request.slots))
        step = frame.runtime.step
        views = step.views.project(to_jsonable(packet), _PACKET_SOURCE, _UNTRUSTED, step.vault)
        return TransferPlan(
            transfer_id=transfer_id,
            target=target,
            target_ref=target_ref,
            release=release,
            to_run_id=self._ids.new_id(IdKind.run),
            packet=packet,
            packet_fp=views.fingerprint,
            snapshot=snapshot,
            depth=depth,
        )

    def _active_release(self, agent_id: str, frame: TurnFrame) -> Release | None:
        """`agent_id@prod` resolved now (D5). A missing or revoked alias is a rejection, not an error:
        `PostgresRegistry` raises `KeyError` for both; the in-memory registry returns the revoked release."""
        try:
            selector = AgentSelector(id=agent_id, alias="prod")
            release = self._registry.resolve_release(selector, frame.state.principal)
            status = self._registry.release_status(release.id)
        except KeyError:
            return None
        return release if status == "active" and release.status == "active" else None

    def _eligible(self, frame: TurnFrame, target: Agent) -> bool:
        """§4 eligibility again, then the `AuthzPort` on the agent and on the subject (D8: the principal, the
        subject and the authentication level travel unchanged)."""
        state = frame.state
        if transfer_ineligibility(target, state.principal, state.subject, state.locale) is not None:
            return False
        if self._authz is None:
            return True
        if not self._authz.authorize_agent(state.principal, target, state.subject).allowed:
            return False
        return self._authz.authorize_subject(state.principal, state.on_behalf_of, state.subject).allowed
