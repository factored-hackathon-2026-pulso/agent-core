"""Wrapper de `lingua-py` (M6 §3.1, ADR 0012): local, sin red, fijado a versión exacta.

Cargar los modelos es caro: el detector se construye una vez por proceso y configuración (`lru_cache`)."""

import importlib.metadata
from functools import lru_cache
from typing import Any

from agent_core.guards.models import GuardsConfigError

_DIST = "lingua-language-detector"
_FAMILY = "lingua"
_SCORE_DIGITS = 6


def installed_version() -> str:
    try:
        return importlib.metadata.version(_DIST)
    except importlib.metadata.PackageNotFoundError:
        raise GuardsConfigError("lingua-language-detector no está instalado") from None


def _split(detector_id: str) -> tuple[str, str]:
    family, _, version = detector_id.partition("@")
    return family, version


def check_detector(detector_id: str, languages: tuple[str, ...]) -> None:
    """Falla al arrancar, no en el primer turno: `GuardsConfigError` si el detector no se puede usar."""
    build_detector(detector_id, languages)


@lru_cache(maxsize=8)
def build_detector(detector_id: str, languages: tuple[str, ...]) -> Any:
    family, version = _split(detector_id)
    if family != _FAMILY:
        raise GuardsConfigError(f"detector no soportado: {family}")
    if installed_version() != version:
        raise GuardsConfigError(f"lingua instalado ({installed_version()}) distinto del fijado ({version})")
    if len(set(languages)) < 2:
        raise GuardsConfigError("lingua necesita al menos dos idiomas candidatos")
    try:
        from lingua import IsoCode639_1, LanguageDetectorBuilder
    except ImportError:
        raise GuardsConfigError("lingua no se pudo importar") from None
    try:
        codes = [getattr(IsoCode639_1, code.upper()) for code in languages]
    except AttributeError:
        raise GuardsConfigError("código de idioma desconocido para lingua") from None
    try:
        return LanguageDetectorBuilder.from_iso_codes_639_1(*codes).with_preloaded_language_models().build()
    except Exception:  # cualquier falla al cargar modelos es de arranque (M6 §5)
        raise GuardsConfigError("lingua no pudo construir el detector") from None


def top2_scores(cleaned: str, detector_id: str, languages: tuple[str, ...]) -> list[tuple[str, float]]:
    """Los dos idiomas más probables entre los candidatos cerrados, como `(código ISO 639-1, puntaje)`."""
    detector = build_detector(detector_id, languages)
    values = [(v.language.iso_code_639_1.name.lower(), round(float(v.value), _SCORE_DIGITS))
              for v in detector.compute_language_confidence_values(cleaned)]
    values.sort(key=lambda item: (-item[1], item[0]))  # empates por código: determinista
    return values[:2]
