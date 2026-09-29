"""Decisión de idioma del turno (M6 §3.1, ADR 0012). `decide` es pura: sin lingua, sin reloj, sin estado."""

from agent_core.domain import LanguageDetection
from agent_core.guards.models import LangDecision, LangThresholds


def decide(*, letters: int, top2: list[tuple[str, float]], cfg: LanguageDetection,
           thresholds: LangThresholds, supported: list[str], prior: str | None) -> LangDecision:
    """Aplica las reglas 3–7 de §3.1 sobre puntajes ya calculados (`top2` ordenado de mayor a menor)."""
    effective = prior if prior is not None else supported[0]

    def out(decision: str, locale: str) -> LangDecision:
        return LangDecision.model_validate({
            "decision": decision, "locale": locale, "locale_prior": prior, "letters": letters,
            "top2": [(lang, score) for lang, score in top2], "detector": cfg.detector,
        })

    if letters == 0 or letters < cfg.min_letters or not top2:
        return out("short", effective)
    lang, score = top2[0]
    second = top2[1][1] if len(top2) > 1 else 0.0
    if score - second < thresholds.min_distance:
        return out("undetermined", effective)
    if lang in supported:
        if lang != effective and thresholds.switch_active and score >= thresholds.switch_threshold:
            return out("switched", lang)
        return out("kept", effective)
    if (thresholds.unsupported_active and score >= thresholds.unsupported_threshold
            and letters >= cfg.min_letters_unsupported):
        return out("unsupported", effective)
    return out("kept", effective)
