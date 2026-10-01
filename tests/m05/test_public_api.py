"""Interfaz pública de M5, fronteras y reglas duras 1 y 2."""

import ast
import subprocess
import sys
from pathlib import Path

import agent_core.decision as decision
import agent_core.decision.calibration as calibration

PACKAGE = Path(decision.__file__).parent
PUBLIC = [
    "ArtifactLoader", "ClassifierProvider", "DecisionConfigError", "DecisionOutput", "DecisionProvider",
    "DecisionService", "EventScope", "HttpJevTransport", "JevProvider", "JevTransport", "JevTransportError",
    "LlmStructuredProvider", "ProviderError", "ProviderTimeout", "RawPrediction", "RuleProvider",
    "UnderstandContext", "UnderstandResult", "UnderstandService", "WILDCARD_LABEL",
]
OFFLINE = [
    "CalibrationArtifact", "CalibrationSource", "DevExample", "DirectoryCalibrationSource",
    "InMemoryCalibrationSource", "IsotonicMap", "Target", "calibrate",
]
SUBMODULES = {"calibration", "providers", "schema", "service", "types", "understand"}


def test_public_interface_is_exactly_the_documented_one() -> None:
    assert sorted(decision.__all__) == sorted(PUBLIC)
    assert [name for name in PUBLIC if not hasattr(decision, name)] == []
    assert sorted(calibration.__all__) == sorted(OFFLINE)
    assert [name for name in OFFLINE if not hasattr(calibration, name)] == []


def test_no_unlisted_public_name_leaks() -> None:
    leaked = [n for n in vars(decision) if not n.startswith("_") and n not in PUBLIC and n not in SUBMODULES]
    assert leaked == []
    submodules = {"artifact", "calibrate", "isotonic", "metrics", "report", "thresholds"}
    leaked = [n for n in vars(calibration) if not n.startswith("_") and n not in OFFLINE
              and n not in submodules]
    assert leaked == []


def test_importing_m5_does_not_import_forbidden_modules() -> None:
    code = ("import sys, agent_core.decision, agent_core.decision.calibration; "
            "names = ['interpreter', 'flows', 'guards', 'actions', 'response', 'handoff', 'audit', "
            "'knowledge', 'turn', 'api', 'adapters', 'registry']; "
            "bad = [m for m in sys.modules if m.split('.')[0] == 'agent_core' and len(m.split('.')) > 1 "
            "and m.split('.')[1] in names]; assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], check=True)


def _modules() -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text(encoding="utf-8"))) for p in sorted(PACKAGE.rglob("*.py"))]


def test_only_allowed_agent_core_imports_and_views_only_through_its_interface() -> None:
    allowed = ("agent_core.domain", "agent_core.ports", "agent_core.views", "agent_core.decision")
    bad: list[str] = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            names = ([node.module] if isinstance(node, ast.ImportFrom) and node.module else
                     [a.name for a in node.names] if isinstance(node, ast.Import) else [])
            for name in names:
                if name.startswith("agent_core") and not name.startswith(allowed):
                    bad.append(f"{path.name}: {name}")
                if name.startswith("agent_core.views."):
                    bad.append(f"{path.name}: {name} (internos de views)")
    assert bad == []


def test_no_clock_or_randomness_calls_in_m5() -> None:
    banned_attrs = {("datetime", "now"), ("datetime", "utcnow"), ("time", "time"), ("time", "perf_counter"),
                    ("time", "monotonic"), ("uuid", "uuid4"), ("os", "urandom")}
    bad: list[str] = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and (
                    (node.value.id, node.attr) in banned_attrs):
                bad.append(f"{path.name}: {node.value.id}.{node.attr}")
            if isinstance(node, ast.Import) and any(a.name in {"random", "secrets"} for a in node.names):
                bad.append(f"{path.name}: import {node.names[0].name}")
            if isinstance(node, ast.ImportFrom) and (
                    node.module in {"random", "secrets"} or any(a.name == "uuid4" for a in node.names)):
                bad.append(f"{path.name}: from {node.module}")
    assert bad == []
