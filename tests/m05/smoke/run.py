"""Prueba de humo de JEV (P0/P0b) y de detección de idioma (ADR 0005 #5). Solo datos sintéticos.

Se activa con `AGENT_CORE_JEV_SMOKE=1` y `JEV_API_KEY` en el entorno:

    AGENT_CORE_JEV_SMOKE=1 uv run python -m tests.m05.smoke [--model jev-latest] [--out res.json]

Este módulo es el punto de composición: solo aquí se enlaza la key (vía entorno) con el transporte. Mide
precisión por idioma, latencia p50/p95 desde este entorno, errores y 429/529, coste, calibración cruda de
`p_raw` y, en la misma corrida, `agent_core.guards.detect_language` (M6) y JEV como segunda opinión de
idioma."""

import argparse
import importlib.metadata
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from agent_core.decision import (
    DecisionConfigError,
    DecisionProvider,
    HttpJevTransport,
    JevProvider,
    ProviderError,
    ProviderTimeout,
    RawPrediction,
)
from agent_core.decision.calibration.metrics import ece
from agent_core.domain import JsonValue, LanguageDetection, ProviderSpec
from agent_core.ports import Clock
from tests.m05.smoke.cases import COMMANDS, FLOWS, Case, all_cases
from tests.m05.smoke.metrics import accuracy, bucket, nearest_rank, reliability

_NS_PER_MS = 1_000_000
_TIMING_REPEATS = 20
_INPUT_USD_PER_MTOK = Decimal("0.042")  # https://docs.typesafe.ai/models (la salida no se cobra)
_BUCKETS = ("1", "2-3", "4-7", "8+")

SCHEMA: dict[str, JsonValue] = {
    "type": "object", "additionalProperties": False, "required": ["command"],
    "properties": {"command": {"type": "string", "enum": list(COMMANDS)},
                   "flow": {"type": "string", "enum": list(FLOWS)}},
}
LANG_SCHEMA: dict[str, JsonValue] = {
    "type": "object", "additionalProperties": False, "required": ["language"],
    "properties": {"language": {"type": "string", "enum": ["es", "pt"]}},
}
_QUESTIONS: dict[str, JsonValue] = {
    "command": {
        "instructions": "¿Qué quiere hacer la persona con su último mensaje? Usa el contexto de la "
                        "conversación (turnos recientes, nodo actual, si hay una confirmación pendiente).",
        "criteria": {
            "start_flow": "Pide iniciar un trámite concreto del catálogo: rastrear un pedido, cancelar una "
                          "suscripción o cambiar una dirección.",
            "continue": "Aporta el dato que se le pidió o sigue con el trámite en curso, sin pedir nada "
                        "nuevo.",
            "affirm": "Responde que sí o acepta lo que se le preguntó.",
            "deny": "Responde que no o rechaza lo que se le preguntó.",
            "clarify": "El mensaje es ambiguo o incompleto: no se entiende qué quiere.",
            "cancel": "Quiere abandonar o cancelar el trámite que está en curso.",
            "handoff": "Pide hablar con una persona o un agente humano.",
            "out_of_scope": "Pide algo sin relación con los trámites del catálogo.",
        },
    },
    "flow": {
        "instructions": "Si la persona quiere iniciar un trámite, ¿cuál es? Elige el más cercano.",
        "criteria": {
            "track_order": "Saber dónde está o cuándo llega un pedido.",
            "cancel_subscription": "Dar de baja o cancelar una suscripción.",
            "change_address": "Cambiar la dirección de entrega o de facturación.",
        },
    },
}
_LANG_QUESTIONS: dict[str, JsonValue] = {
    "language": {"instructions": "¿En qué idioma está escrito el mensaje? Elige el idioma principal.",
                 "criteria": {"es": "español", "pt": "portugués"}},
}


def make_specs(model: str, timeout_ms: int) -> tuple[ProviderSpec, ProviderSpec]:
    common: dict[str, JsonValue] = {"model": model, "timeout_ms": timeout_ms,
                                    "input_usd_per_mtok": _INPUT_USD_PER_MTOK}
    return (ProviderSpec(provider="jev", config={**common, "questions": _QUESTIONS}),
            ProviderSpec(provider="jev", config={**common, "questions": _LANG_QUESTIONS}))


@dataclass(frozen=True)
class LangProbe:
    """Lo que el detector local (M6) dijo de un texto; `ms` es el tiempo de la decisión de producción."""
    top1: str | None
    decision: str
    locale: str
    letters: int
    ms: float


@dataclass
class Row:
    case: Case
    command: str | None = None
    p_command: float | None = None
    flow: str | None = None
    p_flow: float | None = None
    latency_ms: float | None = None
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    error: str | None = None
    model_version: str | None = None
    jev_lang: str | None = None
    p_jev_lang: float | None = None
    jev_lang_ms: float | None = None
    jev_lang_error: str | None = None
    probe: LangProbe | None = None
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def command_ok(self) -> bool:
        return self.command == self.case.command

    @property
    def flow_ok(self) -> bool:
        return self.flow == self.case.flow


Detector = Callable[[str], LangProbe]


def _call(provider: DecisionProvider, spec: ProviderSpec, inputs: dict[str, JsonValue],
          schema: dict[str, JsonValue], locale: str, clock: Clock
          ) -> tuple[RawPrediction | None, float, str | None, int, Decimal]:
    """`(RawPrediction | None, ms, error, tokens, cost)`; un error del proveedor no aborta la corrida."""
    started = clock.monotonic_ns()
    try:
        raw = provider.predict(spec, inputs, schema, locale)
    except ProviderTimeout:
        return None, (clock.monotonic_ns() - started) / _NS_PER_MS, "timeout", 0, Decimal("0")
    except ProviderError as exc:
        return (None, (clock.monotonic_ns() - started) / _NS_PER_MS, str(exc) or "error", exc.tokens,
                exc.cost_usd)
    return raw, (clock.monotonic_ns() - started) / _NS_PER_MS, None, raw.tokens, raw.cost_usd


def evaluate(provider: DecisionProvider | None, specs: tuple[ProviderSpec, ProviderSpec], clock: Clock,
             cases: Sequence[Case], detect: Detector, *, jev_language: bool = True) -> list[Row]:
    """Corre cada caso (secuencial: la latencia no se mezcla con concurrencia) contra JEV y el detector M6.

    Sin `provider` solo corre el detector local (sin red). `DecisionConfigError` aborta la corrida."""
    command_spec, lang_spec = specs
    rows: list[Row] = []
    for case in cases:
        row = Row(case)
        locale = case.lang if case.lang in ("es", "pt") else "es"
        raw = None
        if provider is not None:
            raw, row.latency_ms, row.error, row.tokens, row.cost_usd = _call(
                provider, command_spec, case.inputs(), SCHEMA, locale, clock)
        if raw is not None:
            row.command, row.flow = _str(raw.value.get("command")), _str(raw.value.get("flow"))
            row.p_command, row.p_flow = raw.p_raw.get("command"), raw.p_raw.get("flow")
            row.model_version = raw.model_version
        if provider is not None and jev_language:
            # locale "xx": el idioma es justo lo que se pregunta, no se le adelanta
            lang_raw, row.jev_lang_ms, row.jev_lang_error, tokens, cost = _call(
                provider, lang_spec, {"text": case.text}, LANG_SCHEMA, "xx", clock)
            row.tokens += tokens
            row.cost_usd += cost
            if lang_raw is not None:
                row.jev_lang = _str(lang_raw.value.get("language"))
                row.p_jev_lang = lang_raw.p_raw.get("language")
        row.probe = detect(case.text)
        rows.append(row)
    return rows


def _str(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


# --- reporte -----------------------------------------------------------------------------------------

def _pct(value: float | None) -> str:
    return "n/d" if value is None else f"{value:.1%}"


def _ms(value: float | None) -> str:
    return "n/d" if value is None else f"{value:.0f}"


def _fine(value: float | None) -> str:
    return "n/d" if value is None else f"{value:.3f}"


def _table(header: Sequence[str], body: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return [*lines, ""]


def _group(rows: Sequence[Row], lang: str) -> list[Row]:
    return [r for r in rows if r.case.lang == lang]


def _answered(rows: Sequence[Row]) -> list[Row]:
    return [r for r in rows if r.error is None and r.command is not None]


def render(rows: Sequence[Row], retryable: dict[int, int]) -> str:
    out: list[str] = ["# Prueba de humo de JEV (datos sintéticos)", ""]
    models = sorted({r.model_version for r in rows if r.model_version})
    sizes = ", ".join(f"{lang} = {len(_group(rows, lang))}" for lang in ("es", "pt", "mix"))
    out += [f"- Modelo devuelto por la API: {', '.join(models) or 'n/d'}", f"- Casos: {sizes}", ""]
    out += _precision(rows) + _latency(rows) + _errors(rows, retryable) + _calibration(rows) + _language(rows)
    return "\n".join(out)


def render_language(rows: Sequence[Row]) -> str:
    """Solo la parte de idioma (detector local M6; JEV aparece como n/d si no se le preguntó)."""
    return "\n".join(["# Detección de idioma (datos sintéticos)", "", *_language(rows)])


def _precision(rows: Sequence[Row]) -> list[str]:
    out = ["## Precisión (sobre casos respondidos)", ""]
    body: list[list[str]] = []
    for lang in ("es", "pt", "mix"):
        group = _answered(_group(rows, lang))
        starts = [r for r in group if r.case.command == "start_flow"]
        joint = [r.command_ok and (r.flow_ok if r.case.command == "start_flow" else True) for r in group]
        short = [r.command_ok for r in group if r.case.words <= 3]
        longer = [r.command_ok for r in group if r.case.words > 3]
        body.append([lang, str(len(group)), _pct(accuracy([r.command_ok for r in group])),
                     _pct(accuracy([r.flow_ok for r in starts])), _pct(accuracy(joint)),
                     f"{_pct(accuracy(short))} (n={len(short)})",
                     f"{_pct(accuracy(longer))} (n={len(longer)})"])
    out += _table(["idioma", "n", "command", "flow (start_flow)", "conjunta", "1-3 palabras", "≥4 palabras"],
                  body)
    core = _answered([r for r in rows if r.case.lang in ("es", "pt")])
    out += ["### Acierto de `command` por clase (es + pt)", ""]
    per_class = []
    for command in COMMANDS:
        items = [r for r in core if r.case.command == command]
        wrong = Counter(r.command for r in items if not r.command_ok)
        per_class.append([command, str(len(items)), _pct(accuracy([r.command_ok for r in items])),
                          ", ".join(f"{k}×{v}" for k, v in wrong.most_common()) or "-"])
    out += _table(["clase", "n", "acierto", "confundida con"], per_class)
    misses = [f"{r.case.id}: {r.case.command}→{r.command} (p={_pct(r.p_command)})"
              for r in _answered(list(rows)) if not r.command_ok]
    out += ["### Fallos de `command`", "", *(f"- {m}" for m in misses), ""] if misses else []
    return out


def _latency(rows: Sequence[Row]) -> list[str]:
    out = ["## Latencia desde este entorno (ms, rango más cercano)", ""]
    body: list[list[str]] = []
    groups = [(lang, _group(rows, lang)) for lang in ("es", "pt", "mix")] + [("todos", list(rows))]
    for label, subset in groups:
        ok = [r.latency_ms for r in subset if r.error is None and r.latency_ms is not None]
        lang_ok = [r.jev_lang_ms for r in subset if r.jev_lang_error is None and r.jev_lang_ms is not None]
        body.append([label, str(len(ok)), _ms(nearest_rank(ok, 50)), _ms(nearest_rank(ok, 95)),
                     str(len(lang_ok)), _ms(nearest_rank(lang_ok, 50)), _ms(nearest_rank(lang_ok, 95))])
    out += _table(["grupo", "n command", "p50", "p95", "n idioma", "p50 idioma", "p95 idioma"], body)
    return out


def _errors(rows: Sequence[Row], retryable: dict[int, int]) -> list[str]:
    calls = len(rows) + sum(r.jev_lang_ms is not None for r in rows)
    failed = [r.error for r in rows if r.error] + [r.jev_lang_error for r in rows if r.jev_lang_error]
    tokens = sum(r.tokens for r in rows)
    cost = sum((r.cost_usd for r in rows), Decimal("0"))
    return [
        "## Errores, límites y coste", "",
        f"- Llamadas: {calls}; fallidas tras reintentos: {len(failed)} "
        f"({_pct(len(failed) / calls if calls else None)})",
        f"- Causas: {', '.join(f'{k}×{v}' for k, v in Counter(failed).most_common()) or 'ninguna'}",
        f"- Respuestas 429/529 vistas (reintentadas por el transporte): "
        f"{', '.join(f'{k}×{v}' for k, v in sorted(retryable.items())) or 'ninguna'}",
        f"- Tokens (entrada + salida): {tokens}; media por llamada: {tokens / calls if calls else 0:.0f}",
        f"- Coste a $0.042 por millón de tokens de entrada: {cost:.6f} USD", ""]


def _calibration(rows: Sequence[Row]) -> list[str]:
    out = ["## Calibración cruda de `p_raw` (p vs acierto; no es `p_cal`)", ""]
    for lang in ("es", "pt"):
        group = _answered(_group(rows, lang))
        for label, pairs in (
                ("command", [(r.p_command, r.command_ok) for r in group if r.p_command is not None]),
                ("flow", [(r.p_flow, r.flow_ok) for r in group
                          if r.case.command == "start_flow" and r.p_flow is not None])):
            clean = [(p, ok) for p, ok in pairs if p is not None]
            out += [f"### {lang} · {label} (n={len(clean)}, ECE 10 bins = {_pct(ece(clean))})", ""]
            out += _table(["tramo de p", "n", "p media", "acierto"],
                          [[f"[{b.lo:.1f}, {b.hi:.1f}{']' if b.hi == 1.0 else ')'}", str(b.n),
                            "-" if b.mean_p is None else f"{b.mean_p:.3f}", _pct(b.accuracy)]
                           for b in reliability(clean)])
    return out


def _language(rows: Sequence[Row]) -> list[str]:
    out = ["## Idioma: detector local (M6) vs JEV como segunda opinión (ADR 0005 #5)", "",
           "Detector local: `top-1` = idioma más probable con `min_letters=1` (mide el detector); "
           "`decisión de producción` = `min_letters=12` y umbrales de ejemplo (0.90 / 0.90 / 0.20), "
           "sin calibrar por la unidad 6.", ""]
    body: list[list[str]] = []
    for lang in ("es", "pt"):
        for label in _BUCKETS:
            items = [r for r in _group(rows, lang) if r.probe and bucket(r.case.words) == label]
            top1 = [r.probe.top1 == lang for r in items if r.probe]
            jev = [r.jev_lang == lang for r in items if r.jev_lang is not None]
            body.append([lang, label, str(len(items)), _pct(accuracy(top1)),
                         f"{_pct(accuracy(jev))} (n={len(jev)})"])
    out += _table(["idioma", "palabras", "n", "detector top-1", "JEV idioma"], body)
    out += ["### Decisión de producción del detector local (`prior` = ninguno)", ""]
    decisions: list[list[str]] = []
    for lang in ("es", "pt", "mix"):
        probes = [r.probe for r in _group(rows, lang) if r.probe]
        count = Counter(p.decision for p in probes)
        decided = [p for p in probes if p.decision in ("kept", "switched")]
        right = [p.locale == lang for p in decided] if lang != "mix" else []
        decisions.append([lang, str(len(probes)), *(str(count.get(k, 0)) for k in (
            "kept", "switched", "short", "undetermined", "unsupported")),
            _pct(count.get("undetermined", 0) / len(probes) if probes else None),
            _pct(accuracy(right)) if lang != "mix" else "n/a"])
    out += _table(["idioma", "n", "kept", "switched", "short", "undetermined", "unsupported",
                   "tasa undetermined", "locale correcto (kept/switched)"], decisions)
    mix = _group(rows, "mix")
    out += ["### Portuñol (sin idioma verdadero)", "",
            f"- Detector top-1: {dict(Counter(r.probe.top1 for r in mix if r.probe))}",
            f"- JEV idioma: {dict(Counter(r.jev_lang for r in mix))}", ""]
    times = [r.probe.ms for r in rows if r.probe]
    p50, p95 = nearest_rank(times, 50), nearest_rank(times, 95)
    out += [f"- Latencia del detector local p50/p95: {_fine(p50)} / {_fine(p95)} ms", ""]
    return out


# --- composición -------------------------------------------------------------------------------------

def make_detector(clock: Clock) -> Detector:
    """Detector M6 (`detect_language`) sobre `es`/`pt` + `en` no soportado; mide la decisión de producción."""
    from agent_core.guards import UNCALIBRATED, LangThresholds, detect_language

    detector = f"lingua@{importlib.metadata.version('lingua-language-detector')}"

    def config(min_letters: int) -> LanguageDetection:
        return LanguageDetection.model_validate({
            "id": "lang", "version": "1.0.0", "detector": detector, "candidates": ["es", "pt"],
            "unsupported": ["en"], "min_letters": min_letters, "min_letters_unsupported": 24})

    raw_cfg, prod_cfg = config(1), config(12)
    prod_thresholds = LangThresholds(switch_threshold=0.90, unsupported_threshold=0.90, min_distance=0.20)
    supported = ["es", "pt"]
    detect_language("calentando el detector", prod_cfg, prod_thresholds, supported, None)  # carga de modelos

    def detect(text: str) -> LangProbe:
        top = detect_language(text, raw_cfg, UNCALIBRATED, supported, None)
        # La decisión tarda menos que la resolución del reloj monotónico (~15 ms en Windows): se promedia.
        started = clock.monotonic_ns()
        for _ in range(_TIMING_REPEATS):
            decision = detect_language(text, prod_cfg, prod_thresholds, supported, None)
        elapsed = (clock.monotonic_ns() - started) / _NS_PER_MS / _TIMING_REPEATS
        return LangProbe(top1=top.top2[0][0] if top.top2 else None, decision=decision.decision,
                         locale=decision.locale, letters=decision.letters, ms=elapsed)

    return detect


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.m05.smoke", description=__doc__)
    parser.add_argument("--model", default="jev-latest",
                        help="alias o ID versionado (se reporta el devuelto)")
    parser.add_argument("--timeout-ms", type=int, default=15_000)
    parser.add_argument("--out", help="JSON con el detalle por caso (texto sintético incluido)")
    parser.add_argument("--no-jev-language", action="store_true", help="omite la 2ª llamada de idioma a JEV")
    parser.add_argument("--limit", type=int, help="solo los primeros N casos (depuración)")
    parser.add_argument("--local-only", action="store_true",
                        help="solo el detector de idioma local (M6): sin red, sin key, sin el flag")
    args = parser.parse_args(argv)
    if args.local_only:
        from agent_core.adapters.system_clock import SystemClock

        local_clock = SystemClock()
        specs = make_specs(args.model, args.timeout_ms)
        local_rows = evaluate(None, specs, local_clock, all_cases()[: args.limit], make_detector(local_clock))
        print(render_language(local_rows))
        _dump(local_rows, args.out)
        return 0
    if os.environ.get("AGENT_CORE_JEV_SMOKE") != "1":
        print("La prueba de humo llama a JEV por red: actívala con AGENT_CORE_JEV_SMOKE=1.", file=sys.stderr)
        return 2
    if not os.environ.get("JEV_API_KEY", "").strip():
        print("Falta JEV_API_KEY en el entorno (no se imprime ni se guarda).", file=sys.stderr)
        return 2
    from agent_core.adapters.system_clock import SystemClock  # composición: el reloj real

    clock = SystemClock()
    transport = HttpJevTransport(lambda: os.environ.get("JEV_API_KEY", ""), clock)
    cases = all_cases()[: args.limit]
    try:
        rows = evaluate(JevProvider(transport), make_specs(args.model, args.timeout_ms), clock, cases,
                        make_detector(clock), jev_language=not args.no_jev_language)
    except DecisionConfigError as exc:
        print(f"configuración inválida: {exc}", file=sys.stderr)
        return 1
    print(render(rows, transport.retryable_responses))
    _dump(rows, args.out)
    return 0


def _dump(rows: Sequence[Row], path: str | None) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([asdict(r) for r in rows], handle, ensure_ascii=False, default=str, indent=1)
