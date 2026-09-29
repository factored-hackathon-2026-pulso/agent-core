"""Servicio de guardas de entrada (M6 §2): idioma, tamaño e injection antes de Understand.

No llama a la red, no cambia el flow (M4 decide qué hacer con la salida) y no enmascara PII (M7): recibe el
texto ya en vista `model`."""

import re
from collections.abc import Mapping

from agent_core.domain import (
    Agent,
    EngineEvent,
    InjectionRuleset,
    LanguageDetection,
    Release,
    RunState,
)
from agent_core.guards.detector import check_detector
from agent_core.guards.events import GuardEvents, Scope
from agent_core.guards.injection import NO_RULESET, scan_injection
from agent_core.guards.language import candidate_languages, detect_language
from agent_core.guards.models import (
    UNCALIBRATED,
    GuardResult,
    GuardsConfigError,
    InjectionResult,
    LangDecision,
    LangThresholds,
)
from agent_core.ports import Clock, IdSource, RegistryPort

# Envoltura que M7 §3.2 pone a los campos `untrusted_text`. Es del propio motor: si se escaneara, la regla
# `fake-delimiter` marcaría todo campo envuelto. Solo se quita la envoltura exterior; un delimitador falso
# dentro del contenido sigue marcándose.
_WRAPPER = re.compile(r'\A<datos_no_confiables fuente="[^"<>]*">(.*)</datos_no_confiables>\Z', re.DOTALL)


class GuardService:
    def __init__(self, registry: RegistryPort, clock: Clock, ids: IdSource,
                 calibrations: Mapping[str, LangThresholds] | None = None) -> None:
        self._registry = registry
        self._events = GuardEvents(ids, clock)
        self._calibrations = dict(calibrations or {})

    def validate_release(self, release: Release, agent: Agent) -> None:
        """Error de arranque (M6 §5): lingua ausente o de otra versión, candidatos incoherentes."""
        cfg = self._registry.get(release.language_detection, LanguageDetection)
        if not set(agent.supported_locales) <= set(cfg.candidates):
            raise GuardsConfigError("los candidatos de idioma no incluyen todos los supported_locales")
        check_detector(cfg.detector, candidate_languages(cfg, agent.supported_locales))

    def run(self, text_model_view: str, state: RunState, agent: Agent, release: Release,
            request_lang: str | None, first_turn: bool, *,
            turn_id: str | None = None) -> tuple[GuardResult, list[EngineEvent]]:
        cfg = self._registry.get(release.language_detection, LanguageDetection)
        prior = self._prior(state, agent, request_lang, first_turn)
        if len(text_model_view) > release.max_input_chars:
            skipped = InjectionResult(flagged=False, ruleset=self._ruleset_label(release))
            return GuardResult(lang=self._skipped(cfg, prior), size_ok=False, injection=skipped), []
        supported = list(agent.supported_locales)
        lang = detect_language(text_model_view, cfg, self._thresholds(cfg), supported, prior)
        injection = self._scan(text_model_view, release)
        events: list[EngineEvent] = []
        if injection.flagged:
            events.append(self._events.injection_flagged(state, turn_id, injection, "user_text"))
        return GuardResult(lang=lang, size_ok=True, injection=injection), events

    def scan_untrusted(self, text: str, state: RunState, release: Release, *,
                       turn_id: str | None = None) -> tuple[InjectionResult, list[EngineEvent]]:
        """Escanea un campo `untrusted_text` antes de mandarlo a un modelo (M6 §3.3)."""
        wrapped = _WRAPPER.match(text)
        injection = self._scan(wrapped.group(1) if wrapped else text, release)
        events: list[EngineEvent] = []
        if injection.flagged:
            scope: Scope = "untrusted_field"
            events.append(self._events.injection_flagged(state, turn_id, injection, scope))
        return injection, events

    def _prior(self, state: RunState, agent: Agent, request_lang: str | None, first_turn: bool) -> str:
        if not first_turn:
            return state.locale
        return request_lang if request_lang in agent.supported_locales else agent.default_locale

    def _thresholds(self, cfg: LanguageDetection) -> LangThresholds:
        if cfg.thresholds_from is None:
            return UNCALIBRATED
        return self._calibrations.get(cfg.thresholds_from, UNCALIBRATED)

    def _skipped(self, cfg: LanguageDetection, prior: str) -> LangDecision:
        return LangDecision(decision="short", locale=prior, locale_prior=prior, letters=0, top2=[],
                            detector=cfg.detector)

    def _ruleset(self, release: Release) -> InjectionRuleset | None:
        if release.injection_ruleset is None:
            return None
        return self._registry.get(release.injection_ruleset, InjectionRuleset)

    def _ruleset_label(self, release: Release) -> str:
        ref = release.injection_ruleset
        return NO_RULESET if ref is None else f"{ref.id}@{ref.version}"

    def _scan(self, text: str, release: Release) -> InjectionResult:
        ruleset = self._ruleset(release)
        if ruleset is None:
            return InjectionResult(flagged=False, ruleset=NO_RULESET)
        return scan_injection(text, ruleset)
