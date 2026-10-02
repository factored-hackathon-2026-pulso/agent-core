"""Motor de replay y escenarios de `agentcore record` (herramienta de desarrollo, no va en el wheel)."""

from pathlib import Path

from agent_core.ports import KeyProvider
from agent_core.views import FieldClassifier
from testing.replay.runner import RecordedEngineRunner
from testing.replay.scenarios import SCENARIO_REGISTRY, SCENARIOS, record_scenario


def build_engine_runner(registry_root: Path, *, keys: KeyProvider | None = None,
                        field_classifier: FieldClassifier | None = None) -> RecordedEngineRunner:
    """Punto de entrada que `agent_core.cli.load_engine` busca en este módulo."""
    return RecordedEngineRunner(registry_root, keys=keys, field_classifier=field_classifier)


__all__ = ["SCENARIOS", "SCENARIO_REGISTRY", "RecordedEngineRunner", "build_engine_runner", "record_scenario"]
