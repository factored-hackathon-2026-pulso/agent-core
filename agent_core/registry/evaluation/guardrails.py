"""What the candidate changes in artifacts the gate does not evaluate: policies, flows, tools and the
agent-tool links (`Agent.tools_allowed`). Names and ids only; the reviewer reads the artifacts themselves."""

from collections.abc import Collection, Sequence

from agent_core.domain import Agent, Flow, Policy, RegistryEntity, ToolDef
from agent_core.registry.entities import content_hash
from agent_core.registry.evaluation.report import GuardrailChange

_KINDS: tuple[tuple[str, type[Policy] | type[Flow] | type[ToolDef]], ...] = (
    ("policy", Policy), ("flow", Flow), ("tool", ToolDef))


def guardrail_changes(base: Sequence[RegistryEntity], candidate: Sequence[RegistryEntity], agent_id: str,
                      derived: Collection[tuple[str, str]] = ()) -> list[GuardrailChange]:
    """Added, removed or changed policies, flows and tools, then the tool links added or removed for
    `agent_id`. `changed` means another content hash (a version bump with the same content is not reported).
    `derived` are the (kind, id) the cascade only re-pinned: they are skipped. Without a base, everything the
    candidate carries is `added`. Sorted by kind and id."""
    found: list[GuardrailChange] = []
    for kind, model in _KINDS:
        old = {e.id: e for e in base if isinstance(e, model)}
        new = {e.id: e for e in candidate if isinstance(e, model)}
        for ident in sorted(old.keys() | new.keys()):
            if (kind, ident) in derived:
                continue
            before, after = old.get(ident), new.get(ident)
            if before is None or after is None:
                change = "added" if before is None else "removed"
            elif content_hash(before) != content_hash(after):
                change = "changed"
            else:
                continue
            found.append(GuardrailChange(
                kind=kind, id=ident, change=change,  # type: ignore[arg-type]
                base_version=before.version if before is not None else None,
                candidate_version=after.version if after is not None else None))
    old_links = _links(base, agent_id)
    new_links = _links(candidate, agent_id)
    for link in sorted(old_links | new_links):
        if link not in old_links or link not in new_links:
            found.append(GuardrailChange(kind="tool_link", id=link,
                                         change="added" if link in new_links else "removed"))
    return found


def _links(entities: Sequence[RegistryEntity], agent_id: str) -> set[str]:
    return {f"{agent_id}/{ref.id}" for e in entities if isinstance(e, Agent) and e.id == agent_id
            for ref in e.tools_allowed}
