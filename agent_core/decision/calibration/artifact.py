"""Artefacto de calibración (spec §2 offline): mapas isotónicos, tabla de umbrales y su serialización.

Las claves compuestas se serializan como listas de `{"key": [...], "value": ...}` ordenadas por clave: la
salida es determinista (JCS) y sin ambigüedad aunque un valor contenga separadores."""

from bisect import bisect_right
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import Field, model_validator

from agent_core.domain import JsonValue, Model, Probability, canonical_bytes, loads


class IsotonicMap(Model):
    """Puntos `(x, y)` con `x` estrictamente creciente e `y` no decreciente, ambos en `[0, 1]`.

    `apply` es constante fuera del rango e interpola linealmente dentro: monótona y acotada."""
    xs: list[Probability]
    ys: list[Probability]

    @model_validator(mode="after")
    def _monotone(self) -> Self:
        if not self.xs or len(self.xs) != len(self.ys):
            raise ValueError("xs e ys deben ser no vacíos y del mismo largo")
        if any(b <= a for a, b in zip(self.xs, self.xs[1:], strict=False)):
            raise ValueError("xs debe ser estrictamente creciente")
        if any(b < a for a, b in zip(self.ys, self.ys[1:], strict=False)):
            raise ValueError("ys no puede decrecer")
        return self

    def apply(self, p: float) -> float:
        xs, ys = self.xs, self.ys
        if p <= xs[0]:
            return ys[0]
        if p >= xs[-1]:
            return ys[-1]
        i = bisect_right(xs, p)  # xs[i-1] <= p < xs[i]
        x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
        return y0 + (y1 - y0) * (p - x0) / (x1 - x0)


class Target(Model):
    """Objetivo de calibración de un campo."""
    metric: Literal["precision", "recall"]
    value: Probability


class CalibrationArtifact(Model):
    run_id: str
    split_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    method: Literal["none", "isotonic", "platt", "temperature"]
    # (field, provider, lang)
    calibrators: dict[tuple[str, str, str], IsotonicMap] = Field(default_factory=dict)
    # (field, value, provider, lang)
    thresholds: dict[tuple[str, str, str, str], Probability] = Field(default_factory=dict)
    target: dict[str, Target] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    metrics: dict[str, JsonValue] = Field(default_factory=dict)

    def threshold(self, field: str, value: str, provider: str, lang: str) -> float:
        """Umbral de la combinación; **ausente = 1.0** (nada pasa; spec §3.1.4)."""
        return self.thresholds.get((field, value, provider, lang), 1.0)

    def calibrator(self, field: str, provider: str, lang: str) -> IsotonicMap | None:
        return self.calibrators.get((field, provider, lang))

    def to_json(self) -> str:
        data: dict[str, object] = {
            "run_id": self.run_id,
            "split_hash": self.split_hash,
            "method": self.method,
            "calibrators": [{"key": list(key), "value": {"xs": m.xs, "ys": m.ys}}
                            for key, m in sorted(self.calibrators.items())],
            "thresholds": [{"key": list(key), "value": value}
                           for key, value in sorted(self.thresholds.items())],
            "target": {name: {"metric": t.metric, "value": t.value} for name, t in self.target.items()},
            "limitations": list(self.limitations),
            "metrics": self.metrics,
        }
        return canonical_bytes(data).decode("utf-8")

    @classmethod
    def from_json(cls, text: str) -> Self:
        raw = loads(text)
        if not isinstance(raw, dict):
            raise ValueError("el artefacto de calibración debe ser un objeto JSON")
        calibrators = {tuple(_key(e)): e["value"] for e in _entries(raw.get("calibrators", []))}
        thresholds = {tuple(_key(e)): e["value"] for e in _entries(raw.get("thresholds", []))}
        return cls.model_validate({**raw, "calibrators": calibrators, "thresholds": thresholds})


def _entries(value: JsonValue) -> list[dict[str, JsonValue]]:
    well_formed = isinstance(value, list) and all(
        isinstance(e, dict) and set(e) == {"key", "value"} for e in value)
    if not isinstance(value, list) or not well_formed:
        raise ValueError("entradas de tabla mal formadas")
    return [e for e in value if isinstance(e, dict)]


def _key(entry: dict[str, JsonValue]) -> list[str]:
    key = entry["key"]
    if not isinstance(key, list) or not all(isinstance(k, str) for k in key):
        raise ValueError("clave de tabla mal formada")
    return [k for k in key if isinstance(k, str)]


class CalibrationSource(Protocol):
    """Origen de artefactos por `run_id` (P6). No es un puerto de M0."""

    def get(self, run_id: str) -> CalibrationArtifact | None: ...


class InMemoryCalibrationSource:
    def __init__(self, artifacts: Mapping[str, CalibrationArtifact] | None = None) -> None:
        self._artifacts = dict(artifacts or {})

    def add(self, *artifacts: CalibrationArtifact) -> None:
        for artifact in artifacts:
            self._artifacts[artifact.run_id] = artifact

    def get(self, run_id: str) -> CalibrationArtifact | None:
        return self._artifacts.get(run_id)


class DirectoryCalibrationSource:
    """Lee `<run_id>.json` de un directorio (solo con `loads` vía `from_json`)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, run_id: str) -> CalibrationArtifact | None:
        if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
            raise ValueError("run_id inválido para el directorio de calibraciones")
        file = self._path / f"{run_id}.json"
        if not file.is_file():
            return None
        return CalibrationArtifact.from_json(file.read_text(encoding="utf-8"))
