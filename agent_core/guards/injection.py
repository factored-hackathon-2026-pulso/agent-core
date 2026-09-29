"""Detector de injection por reglas versionadas (M6 §3.3). Determinista, local y sin red.

Cada regla del ruleset es una regex o una frase literal. El texto se normaliza antes de comparar
(HTML-unescape, NFKC, sin caracteres de formato, minúsculas, espacios colapsados) para que invisibles y
entidades no sirvan de evasión. Reemplazable por un clasificador detrás de la misma firma."""

import html
import re
import unicodedata
from collections.abc import Callable
from functools import lru_cache

from agent_core.domain import InjectionRuleset
from agent_core.guards.models import InjectionResult

NO_RULESET = "none"

_Matcher = Callable[[str], bool]


def normalize_for_scan(text: str) -> str:
    unescaped = html.unescape(text)
    folded = unicodedata.normalize("NFKC", unescaped)
    visible = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    return " ".join(visible.casefold().split())


@lru_cache(maxsize=32)
def _compile(rules: tuple[tuple[str, str, str], ...]) -> tuple[tuple[str, _Matcher], ...]:
    compiled: list[tuple[str, _Matcher]] = []
    for rule_id, kind, pattern in rules:
        if kind == "regex":
            compiled.append((rule_id, _regex_matcher(re.compile(pattern, re.IGNORECASE))))
        else:
            compiled.append((rule_id, _phrase_matcher(normalize_for_scan(pattern))))
    return tuple(compiled)


def _regex_matcher(regex: re.Pattern[str]) -> _Matcher:
    def matches(text: str) -> bool:
        return regex.search(text) is not None

    return matches


def _phrase_matcher(phrase: str) -> _Matcher:
    def matches(text: str) -> bool:
        return phrase in text

    return matches


def scan_injection(text: str, ruleset: InjectionRuleset) -> InjectionResult:
    """Marca el texto si alguna regla coincide. `signals` son ids de regla, nunca fragmentos del texto."""
    matchers = _compile(tuple((r.id, r.kind, r.pattern) for r in ruleset.rules))
    normalized = normalize_for_scan(text)
    signals: list[str] = []
    for rule_id, matches in matchers:
        if rule_id not in signals and matches(normalized):
            signals.append(rule_id)
    return InjectionResult(flagged=bool(signals), signals=signals, ruleset=f"{ruleset.id}@{ruleset.version}")
