"""Proveedor `classifier` (spec §3.3): TF-IDF + regresión logística evaluados en Python puro (P4, confirmado).

Formato `tfidf-logreg-v1` (JSON, lo entrena y exporta el científico de datos; ref + hash de datos):

    {"format": "tfidf-logreg-v1", "data_hash": "<sha256>", "vocab": {token: índice}, "idf": [...],
     "classes": {campo: [valor, ...]}, "coef": {campo: [[peso por token] por clase]},
     "intercept": {campo: [sesgo por clase]}}

`config.text_from` (opcional) nombra la clave de la entrada de la que sale el texto (por defecto `text`).

Evaluación: tokens = `re.findall(r"\\w+")` sobre el texto en NFKC y minúsculas; `x[i] = conteo[i] * idf[i]`
normalizado en L2 (si la norma es 0, `x = 0`); logits = `coef · x + intercept`; softmax estable. La clase
elegida es el argmax con desempate por valor; `top_k` va ordenado por `(-p, valor)`. Determinista."""

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from agent_core.decision.types import DecisionConfigError, ProviderError, RawPrediction
from agent_core.domain import JsonValue, Locale, ProviderSpec, loads

_FORMAT = "tfidf-logreg-v1"
_WORD = re.compile(r"\w+")


class ArtifactLoader(Protocol):
    """Devuelve el texto JSON del artefacto por su referencia (el servicio no toca disco)."""

    def load(self, ref: str) -> str: ...


@dataclass(frozen=True)
class ClassifierArtifact:
    data_hash: str
    vocab: dict[str, int]
    idf: list[float]
    classes: dict[str, list[str]]
    coef: dict[str, list[list[float]]]
    intercept: dict[str, list[float]]

    @classmethod
    def parse(cls, text: str) -> "ClassifierArtifact":
        try:
            doc = loads(text)
        except ValueError:
            raise DecisionConfigError("classifier: el artefacto no es JSON válido") from None
        if not isinstance(doc, dict) or doc.get("format") != _FORMAT:
            raise DecisionConfigError(f"classifier: formato de artefacto desconocido (se espera {_FORMAT})")
        raw: dict[str, Any] = doc
        try:
            artifact = cls(
                data_hash=_typed(raw, "data_hash", str),
                vocab={str(k): int(v) for k, v in _typed(raw, "vocab", dict).items()},
                idf=[float(v) for v in _typed(raw, "idf", list)],
                classes={k: [str(c) for c in v] for k, v in _typed(raw, "classes", dict).items()},
                coef={k: [[float(w) for w in row] for row in v]
                      for k, v in _typed(raw, "coef", dict).items()},
                intercept={k: [float(b) for b in v] for k, v in _typed(raw, "intercept", dict).items()},
            )
        except (TypeError, ValueError):
            raise DecisionConfigError("classifier: artefacto mal formado") from None
        artifact._check()
        return artifact

    def _check(self) -> None:
        size = len(self.idf)
        if not self.vocab or size != len(self.vocab) or any(not 0 <= i < size for i in self.vocab.values()):
            raise DecisionConfigError("classifier: vocab e idf inconsistentes")
        fields = set(self.classes)
        if not fields or fields != set(self.coef) or fields != set(self.intercept):
            raise DecisionConfigError("classifier: campos inconsistentes entre classes, coef e intercept")
        for field, labels in self.classes.items():
            rows, bias = self.coef[field], self.intercept[field]
            if not labels or len(rows) != len(labels) or len(bias) != len(labels):
                raise DecisionConfigError(f"classifier: dimensiones inconsistentes en {field!r}")
            if any(len(row) != size for row in rows):
                raise DecisionConfigError(f"classifier: coef de {field!r} no cubre el vocabulario")


def _text(spec: ProviderSpec, inputs: dict[str, JsonValue]) -> str:
    """El texto a clasificar: `inputs["text"]`, o la(s) clave(s) de `config.text_from` (texto o lista de
    textos, unidos con un espacio) cuando el `input_view` de la decisión no se llama `text`."""
    source = spec.config.get("text_from", "text")
    raw = [source] if isinstance(source, str) else source
    if not isinstance(raw, list) or not raw or not all(isinstance(k, str) and k for k in raw):
        raise DecisionConfigError("classifier: config.text_from debe ser un texto o una lista de textos")
    keys = [k for k in raw if isinstance(k, str)]
    parts = [inputs.get(k) for k in keys]
    if not all(isinstance(p, str) for p in parts):
        raise ProviderError(f"classifier: la entrada necesita {' y '.join(repr(k) for k in keys)} (string)")
    return " ".join(p for p in parts if isinstance(p, str))


def _typed[T](doc: dict[str, Any], key: str, kind: type[T]) -> T:
    value = doc.get(key)
    if not isinstance(value, kind):
        raise ValueError(key)
    return value


class ClassifierProvider:
    name = "classifier"

    def __init__(self, loader: ArtifactLoader) -> None:
        self._loader = loader
        self._cache: dict[str, ClassifierArtifact] = {}

    def __repr__(self) -> str:
        return "ClassifierProvider()"

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: Locale) -> RawPrediction:
        artifact = self._artifact(spec)
        text = _text(spec, inputs_model_view)
        x = _features(text, artifact)
        value: dict[str, JsonValue] = {}
        p_raw: dict[str, float | None] = {}
        top_k: dict[str, list[tuple[str, float]]] = {}
        for field in sorted(artifact.classes):
            logits = [sum(w * v for w, v in zip(row, x, strict=True)) + b
                      for row, b in zip(artifact.coef[field], artifact.intercept[field], strict=True)]
            top = sorted(zip(artifact.classes[field], _softmax(logits), strict=True),
                         key=lambda item: (-item[1], item[0]))
            value[field], p_raw[field], top_k[field] = top[0][0], top[0][1], top
        return RawPrediction(value=value, p_raw=p_raw, top_k=top_k,
                             model_version=f"classifier:{artifact.data_hash[:12]}")

    def _artifact(self, spec: ProviderSpec) -> ClassifierArtifact:
        ref = spec.config.get("artifact")
        if not isinstance(ref, str) or not ref:
            raise DecisionConfigError("classifier: config.artifact es obligatorio")
        if ref not in self._cache:
            try:
                text = self._loader.load(ref)
            except Exception:
                raise DecisionConfigError("classifier: no se pudo cargar el artefacto") from None
            self._cache[ref] = ClassifierArtifact.parse(text)
        return self._cache[ref]


def _features(text: str, artifact: ClassifierArtifact) -> list[float]:
    counts = Counter(_WORD.findall(unicodedata.normalize("NFKC", text).lower()))
    x = [0.0] * len(artifact.idf)
    for token, count in counts.items():
        index = artifact.vocab.get(token)
        if index is not None:
            x[index] = count * artifact.idf[index]
    norm = math.sqrt(sum(v * v for v in x))
    return [v / norm for v in x] if norm else x


def _softmax(logits: list[float]) -> list[float]:
    peak = max(logits)
    exps = [math.exp(v - peak) for v in logits]
    total = sum(exps)
    return [v / total for v in exps]
