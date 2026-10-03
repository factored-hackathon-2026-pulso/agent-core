"""N-02 (§31.12): lecturas de alias y de versiones por entidad, sin crear propuesta ni gastar cuota."""

from tests.registry.helpers import AGENT, admin, prompt_draft
from tests.registry.service_world import SUITE, World, publish_cycle
from tests.registry.test_http import _client, _h


def test_alias_read_returns_the_release_and_its_status() -> None:
    c, _ = _client()
    got = c.get(f"/v1/registry/aliases/{AGENT}/prod", headers=_h("ana"))
    assert got.status_code == 200
    assert got.json() == {"agent_id": AGENT, "alias": "prod", "release_id": "rel-demo", "status": "active"}


def test_alias_read_of_an_unset_alias_is_404_and_needs_auth() -> None:
    c, _ = _client()
    missing = c.get(f"/v1/registry/aliases/{AGENT}/canary", headers=_h("ana"))
    assert missing.status_code == 404 and missing.json()["code"] == "not_found"
    assert c.get(f"/v1/registry/aliases/{AGENT}/prod").status_code == 401


def test_alias_read_follows_a_promotion_and_a_revoke() -> None:
    w = World()
    rid = publish_cycle(w, [prompt_draft(), SUITE])
    assert w.service.get_alias(AGENT, "staging").release_id == rid
    w.service.revoke(admin(), rid, "x")
    assert w.service.get_alias(AGENT, "staging").status == "revoked"


def test_versions_of_an_entity_are_listed_oldest_first_and_do_not_create_proposals() -> None:
    c, w = _client()
    publish_cycle(w, [prompt_draft(), SUITE])
    got = c.get("/v1/registry/versions/prompt/p/resumen_radicado", headers=_h("ana"))
    assert got.status_code == 200
    assert [v["ref"]["version"] for v in got.json()] == ["1.0.0", "1.1.0"]
    assert c.get("/v1/registry/versions/prompt/no/existe", headers=_h("ana")).json() == []
