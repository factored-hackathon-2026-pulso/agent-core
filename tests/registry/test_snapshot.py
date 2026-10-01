import pytest

from agent_core.domain import AgentSelector, EntityKind, EntityRef, Prompt
from agent_core.registry.snapshot import SnapshotRegistry
from testing.builders import principal
from tests.registry.helpers import AGENT, demo_pinned


def _snap() -> SnapshotRegistry:
    pinned = demo_pinned()
    return SnapshotRegistry(pinned.release, pinned.entities)


def test_resolves_any_alias_and_exact_agent_version() -> None:
    snap = _snap()
    assert snap.resolve_release(AgentSelector.parse(f"{AGENT}@staging"), principal()).id == "demo"
    assert snap.resolve_release(AgentSelector.parse(f"{AGENT}@1.0.0"), principal()).id == "demo"
    with pytest.raises(KeyError):
        snap.resolve_release(AgentSelector.parse(f"{AGENT}@9.9.9"), principal())
    with pytest.raises(KeyError):
        snap.resolve_release(AgentSelector.parse("otro"), principal())


def test_status_is_active_only_for_its_release() -> None:
    snap = _snap()
    assert snap.release_status("demo") == "active"
    with pytest.raises(KeyError):
        snap.release_status("x")


def test_get_returns_copies() -> None:
    snap = _snap()
    ref = EntityRef(id="p/resumen_radicado", version="1.0.0")
    first = snap.get(ref, Prompt)
    first.locales["es"] = "hackeado"
    assert snap.get(ref, Prompt).locales["es"] != "hackeado"


def test_release_with_agent() -> None:
    release = _snap().resolve_release(AgentSelector.parse(AGENT), principal())
    assert AGENT in release.entities[EntityKind.agent]
