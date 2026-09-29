import agent_core.flows as flows

# Interfaz pública de M1 (spec §2, fase 1) más validate_registry, que usa la CLI (§3.12).
# validate_flow_for_agent y validate_agent llegan en la fase 5.
PUBLIC = [
    "Violation", "FlowSchemaError", "parse_flow", "RegistryView", "release_view", "validate_flow",
    "derive_claims", "JSONLOGIC_OPS", "jsonlogic_problems", "Path", "parse_path", "value_paths",
    "template_vars",
    "ReleaseDecl", "AuthoringRegistry", "load_yaml", "load_registry", "PinnedRelease", "pin_release",
    "validate_registry",
]


def test_public_interface_is_exactly_the_spec() -> None:
    assert sorted(flows.__all__) == sorted(PUBLIC)
    assert [name for name in PUBLIC if not hasattr(flows, name)] == []


def test_no_unlisted_public_name_leaks() -> None:
    submodules = {"claims", "cli_validate", "context", "graph", "jsonlogic", "paths", "pin", "refs",
                  "registry", "rules", "schema", "validate", "view", "violations", "yaml_loader"}
    leaked = [n for n in vars(flows) if not n.startswith("_") and n not in PUBLIC and n not in submodules]
    assert leaked == []
