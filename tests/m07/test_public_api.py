"""Interfaz pública de M7 (spec §2)."""

import agent_core.views as views

PUBLIC = [
    "DEFAULT_CATALOG", "FieldClass", "FieldClassifier", "FieldRule", "QuasiRule", "TOKEN_PATTERN",
    "TokenEntry", "TokenMapError", "TokenVault", "Views", "Rendered", "ViewService", "ViewsConfigError",
    "fingerprint", "verify_fingerprint",
]


def test_public_interface_is_exactly_the_spec() -> None:
    assert sorted(views.__all__) == sorted(PUBLIC)
    assert [name for name in PUBLIC if not hasattr(views, name)] == []


def test_no_unlisted_public_name_leaks() -> None:
    submodules = {"classification", "detector", "fingerprints", "quasi", "service", "tokens", "untrusted",
                  "vault"}
    leaked = [n for n in vars(views) if not n.startswith("_") and n not in PUBLIC and n not in submodules]
    assert leaked == []
