"""Agent-to-agent transfer types (ADR 0021, spec §3): routing card, input contract, directory, packet."""

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, PositiveInt

from agent_core.domain.base import EntityId, Locale, Model, Sha256Hex
from agent_core.domain.json import JsonValue, canonical_bytes, sha256_hex
from agent_core.domain.refs import EntityRef

SlotType = Literal["string", "integer", "decimal", "date", "boolean"]


class RoutingCard(Model):
    """What a specialist solves, as shown to the reception agent (spec §3.1)."""

    directory: EntityId
    summary: str = Field(min_length=1, max_length=500)
    examples: list[str] = Field(default_factory=list, max_length=20)


class AcceptedSlot(Model):
    """One slot of the input contract: its type and whether the packet must carry it."""

    type: SlotType
    required: bool = False


class TransferContract(Model):
    """The slots a specialist accepts in a transfer packet (spec §3.1, D6)."""

    slots: dict[str, AcceptedSlot] = Field(default_factory=dict)


class DirectoryEntry(Model):
    """One specialist as the directory lists it: its active release, card and contract."""

    agent_id: EntityId
    release_id: str = Field(min_length=1)
    summary: str
    examples: list[str] = Field(default_factory=list)
    accepts: TransferContract
    supported_locales: list[Locale]


class DirectorySnapshot(Model):
    """The directory as one run read it: filtered for its principal, with the hash of the full directory."""

    directory: EntityId
    hash: Sha256Hex
    entries: list[DirectoryEntry] = Field(default_factory=list)

    @property
    def choices(self) -> list[str]:
        return [entry.agent_id for entry in self.entries]


class TransferPacket(Model):
    """What travels to the specialist (D7). `trigger` is the user text in the `model` view."""

    reason: str = Field(min_length=1)
    trigger: str
    slots: dict[str, JsonValue] = Field(default_factory=dict)


class RunOrigin(Model):
    """Why a run exists when another run transferred to it (spec §3.3, P2, P5)."""

    kind: Literal["transfer"]
    transfer_id: str
    from_run_id: str
    from_agent: EntityRef
    from_release_id: str
    from_event_hash: Sha256Hex
    depth: PositiveInt


def directory_hash(pairs: Iterable[tuple[str, str]]) -> str:
    """sha256 of the sorted `(agent_id, release_id)` pairs: changes on publish, promote or revoke."""
    return sha256_hex(canonical_bytes(sorted([list(pair) for pair in pairs])))


def _matches(kind: SlotType, value: JsonValue) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "decimal":
        return isinstance(value, Decimal | int) and not isinstance(value, bool)
    return isinstance(value, str) and _is_iso_date(value)


def _is_iso_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def packet_problem(contract: TransferContract, slots: Mapping[str, JsonValue]) -> str | None:
    """`None` if the slots fit the contract; otherwise a reason code without values."""
    for name, spec in contract.slots.items():
        if spec.required and name not in slots:
            return "missing_required_slot"
    for name, value in slots.items():
        accepted = contract.slots.get(name)
        if accepted is None:
            return "slot_not_accepted"
        if not _matches(accepted.type, value):
            return "slot_type_mismatch"
    return None
