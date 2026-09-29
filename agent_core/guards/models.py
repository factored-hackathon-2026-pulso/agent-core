"""Tipos de salida de las guardas de entrada (M6 §2)."""

from typing import Literal

from pydantic import Field, NonNegativeInt

from agent_core.domain import GuardsOutput, InjectionGuard, LangGuard, LangScore
from agent_core.domain.base import Locale, Model, Probability


class GuardsConfigError(Exception):
    """Configuración de guardas inutilizable (lingua ausente, versión distinta, candidatos incoherentes).

    Es un error de arranque: nunca se degrada en silencio (M6 §5)."""


class LangThresholds(Model):
    """Umbrales de una corrida de calibración (unidad 6). Un umbral de 1.0 desactiva su regla (M6 §3.1.7)."""

    switch_threshold: Probability = 1.0
    unsupported_threshold: Probability = 1.0
    min_distance: Probability = 0.10

    @property
    def switch_active(self) -> bool:
        return self.switch_threshold < 1.0

    @property
    def unsupported_active(self) -> bool:
        return self.unsupported_threshold < 1.0


UNCALIBRATED = LangThresholds()


class LangDecision(Model):
    decision: Literal["kept", "switched", "short", "undetermined", "unsupported"]
    locale: Locale
    locale_prior: Locale | None = None
    letters: NonNegativeInt
    top2: list[tuple[str, Probability]] = Field(default_factory=list)
    detector: str


class InjectionResult(Model):
    flagged: bool
    signals: list[str] = Field(default_factory=list)
    ruleset: str


class GuardResult(Model):
    lang: LangDecision
    size_ok: bool
    injection: InjectionResult

    def to_output(self) -> GuardsOutput:
        """Forma que M4 pone en `TurnStartedPayload.guards` (M0 §2.10)."""
        lang = self.lang
        return GuardsOutput(
            lang=LangGuard(
                detector=lang.detector, letters=lang.letters,
                top2=[LangScore(lang=code, score=score) for code, score in lang.top2],
                decision=lang.decision, locale_prior=lang.locale_prior, locale=lang.locale,
            ),
            injection=InjectionGuard(
                flagged=self.injection.flagged, signals=list(self.injection.signals),
                ruleset=self.injection.ruleset,
            ),
            size_ok=self.size_ok,
        )
