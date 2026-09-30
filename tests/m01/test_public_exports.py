def test_registry_needs_are_exported() -> None:
    from agent_core.flows import RefSite, entity_ref_sites, kind_of  # noqa: F401


def test_idkind_has_registry_kinds() -> None:
    from agent_core.ports import IdKind
    assert IdKind("proposal") and IdKind("eval_run")
