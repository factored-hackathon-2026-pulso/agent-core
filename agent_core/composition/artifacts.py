"""Artefactos de calibración y del proveedor `classifier` desde directorios (P6, spec §3.3).

Son datos que entrena y exporta el equipo de datos (unidad 6) y que viajan con la release: un directorio
montado o incluido en la imagen, nunca generado por el servidor. Sin el directorio configurado, `serve`
no arranca fuera de demo: una calibración ausente significa "nada pasa" (umbral 1.0)
 y eso hay que pedirlo."""

import os
from collections.abc import Mapping
from pathlib import Path

from agent_core.decision import ClassifierProvider
from agent_core.decision.calibration import DirectoryCalibrationSource
from agent_core.domain import SchemaError

CALIBRATION_DIR_ENV = "AGENTCORE_CALIBRATION_DIR"
CLASSIFIER_ARTIFACTS_DIR_ENV = "AGENTCORE_CLASSIFIER_ARTIFACTS_DIR"


def _directory(env: Mapping[str, str], name: str) -> Path:
    value = (env.get(name) or "").strip()
    if not value:
        raise SchemaError(f"falta {name}: directorio con los artefactos")
    path = Path(value)
    if not path.is_dir():
        raise SchemaError(f"{name}: no es un directorio")
    return path


def calibration_from_env(env: Mapping[str, str]) -> DirectoryCalibrationSource:
    return DirectoryCalibrationSource(_directory(env, CALIBRATION_DIR_ENV))


class DirectoryArtifactLoader:
    """`ArtifactLoader`: lee `<ref>.json`. La referencia sale de la config de un decision model (datos de un
    agente), así que no puede salirse del directorio."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self, ref: str) -> str:
        if not ref or "/" in ref or "\\" in ref or ref.startswith("."):
            raise ValueError("referencia de artefacto inválida")
        return (self._path / f"{ref}.json").read_text(encoding="utf-8")


def classifier_from_env(env: Mapping[str, str]) -> ClassifierProvider:
    return ClassifierProvider(DirectoryArtifactLoader(_directory(env, CLASSIFIER_ARTIFACTS_DIR_ENV)))


def calibration(ctx: object) -> DirectoryCalibrationSource:
    """Factory for `serve --calibration agent_core.composition.artifacts:calibration`."""
    return calibration_from_env(os.environ)


def classifier_provider(ctx: object) -> ClassifierProvider:
    """Factory for `serve --classifier agent_core.composition.artifacts:classifier_provider`."""
    return classifier_from_env(os.environ)
