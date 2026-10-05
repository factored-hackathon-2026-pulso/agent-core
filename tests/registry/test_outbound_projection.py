"""Eventos `release.*` salientes proyectados desde `reg_events`."""

from agent_core.domain import to_jsonable
from agent_core.outbound import OutboundEvent
from agent_core.registry.models import RegistryEvent
from agent_core.registry.outbound import project_registry_event
from tests.registry.helpers import AGENT, admin, prompt_draft, suite_draft
from tests.registry.service_world import World, publish_cycle


def _events() -> tuple[list[RegistryEvent], list[tuple[int, OutboundEvent]]]:
    w = World()
    first = publish_cycle(w, [prompt_draft(), suite_draft()])
    w.service.promote(admin(), AGENT, "prod", first, "go")
    second = publish_cycle(w, [prompt_draft(version="1.3.0", text="Otra variante del saludo.")], key="k2")
    w.service.revoke(admin(), second, "problema con el texto del cliente Juan Perez")
    with w.store.transaction() as tx:
        events = tx.events()
    return events, [(i, p) for i, e in enumerate(events) if (p := project_registry_event(e, i))]


def test_published_promoted_and_revoked_are_public_in_order() -> None:
    _, out = _events()
    assert [p.type for _, p in out] == [
        "release.published", "release.promoted", "release.published", "release.revoked"]
    assert all(p.source == "registry" and p.event_id == f"reg-{i}" for i, p in out)


def test_other_registry_events_are_not_public() -> None:
    events, out = _events()
    assert len(out) < len(events)
    assert not any(project_registry_event(e, 0) for e in events
                   if e.type not in {"published", "promoted", "revoked"})


def test_no_actor_reason_or_hash_leaks() -> None:
    _, out = _events()
    blob = str([to_jsonable(p) for _, p in out])
    for secret in ("ana", "root", "Juan Perez", "candidate_hash", "actor", "reason", "principal_type"):
        assert secret not in blob, secret
    assert set(to_jsonable(out[0][1])["data"]) == {"release_id", "proposal_id", "agent_id", "alias", "before"}


def test_release_events_carry_the_agent_and_the_alias_they_touch() -> None:
    events, out = _events()
    published, promoted, _, revoked = (to_jsonable(p)["data"] for _, p in out)
    assert (published["agent_id"], published["alias"]) == (AGENT, "staging")
    assert (promoted["agent_id"], promoted["alias"], promoted["before"]) == (AGENT, "prod", "rel-demo")
    assert promoted["release_id"] == published["release_id"]
    assert (revoked["agent_id"], revoked["alias"], revoked["before"]) == (AGENT, None, None)


def test_a_second_promotion_reports_the_previous_release_as_before() -> None:
    w = World()
    first = publish_cycle(w, [prompt_draft(), suite_draft()])
    w.service.promote(admin(), AGENT, "prod", first, "go")
    second = publish_cycle(w, [prompt_draft(version="1.3.0", text="Otra variante del saludo.")], key="k2")
    w.service.promote(admin(), AGENT, "prod", second, "go")
    with w.store.transaction() as tx:
        events = tx.events()
    promoted = [to_jsonable(p)["data"] for i, e in enumerate(events) if (p := project_registry_event(e, i))
                and p.type == "release.promoted"]
    assert [d["before"] for d in promoted] == ["rel-demo", first]


def test_an_event_stored_before_the_new_fields_still_projects() -> None:
    old = RegistryEvent.model_validate({
        "type": "promoted", "actor": "ana", "principal_type": "human", "release_id": "rel-1",
        "at": "2026-10-01T00:00:00Z"})
    data = to_jsonable(project_registry_event(old, 7))["data"]
    assert data == {"release_id": "rel-1", "proposal_id": None, "agent_id": None, "alias": None,
                    "before": None}
