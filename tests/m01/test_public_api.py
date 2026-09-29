import agent_core.flows as flows

# Interfaz pública de M1 (spec §2) más validate_registry, que usa la CLI (§3.12).
PUBLIC = [
    "Violation", "FlowSchemaError", "parse_flow", "RegistryView", "release_view", "validate_flow",
    "derive_claims", "JSONLOGIC_OPS", "jsonlogic_problems", "Path", "parse_path", "value_paths",
    "template_vars",
    "ReleaseDecl", "AuthoringRegistry", "load_yaml", "load_registry", "PinnedRelease", "pin_release",
    "validate_registry", "validate_agent", "validate_flow_for_agent",
]


def test_public_interface_is_exactly_the_spec() -> None:
    assert sorted(flows.__all__) == sorted(PUBLIC)
    assert [name for name in PUBLIC if not hasattr(flows, name)] == []


def test_no_unlisted_public_name_leaks() -> None:
    submodules = {"agent", "claims", "cli_validate", "closure", "context", "graph", "jsonlogic", "paths",
                  "pin", "refs", "registry", "rules", "schema", "validate", "view", "violations",
                  "yaml_loader"}
    leaked = [n for n in vars(flows) if not n.startswith("_") and n not in PUBLIC and n not in submodules]
    assert leaked == []


def test_fakes_package_does_not_eagerly_import_flows() -> None:
    import subprocess
    import sys

    code = ("import sys, testing.fakes; "
            "assert 'agent_core.flows' not in sys.modules; "
            "from testing.fakes import registry_from_directory; "
            "assert 'agent_core.flows' in sys.modules and callable(registry_from_directory)")
    subprocess.run([sys.executable, "-c", code], check=True)
