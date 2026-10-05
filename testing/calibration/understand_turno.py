"""Conjunto SINTÉTICO etiquetado para calibrar `understand-turno` (campos `command`, `flow` e `interrupt`).

Las frases están en `understand_turno_phrases.py`. Aquí se les da la forma de entrada de Understand (la misma
que arma `UnderstandService.run`: `text`, `recent_turns`, `current_node`, `confirm_pending`) y se parten en
dos conjuntos disjuntos:

- `dev`: con él corre `calibrate` (umbrales y mapas isotónicos).
- `test`: cerrado. Solo se usa para medir el artefacto ya hecho; nunca para elegir umbrales.

La partición es por frase base y estratificada por comando e idioma (se ordenan por hash y el primer 30 %
va a `test`): una frase y su variante de escritura caen siempre del mismo lado, así que `test` no contiene
reformulaciones de lo que vio `dev`, y ningún comando se queda sin muestra cerrada por azar.

Etiquetas (solo los campos que aplican, como en `UnderstandService`): `command` siempre; `flow` solo con
`start_flow`; `interrupt` solo con `interrupt` (valor `fraude`). Español: `synthetic=False`; portugués
(ADR 0012): `synthetic=True` y muestra menor, así que `calibrate` copia la calibración del español si no
alcanza `min_samples`.

Límite que conviene recordar: son mensajes escritos para esto, más limpios que los de un cliente real. Los
umbrales que salgan de aquí son de demostración y el artefacto debe decirlo en `limitations`."""

import re
from functools import cache
from typing import Literal

from agent_core.decision.calibration import DevExample
from agent_core.domain import JsonValue, Locale, sha256_hex
from testing.calibration import understand_turno_phrases as p

Split = Literal["dev", "test"]
COMMANDS = ("start_flow", "continue", "affirm", "deny", "clarify", "cancel", "handoff", "out_of_scope",
            "interrupt")
FLOWS = ("disputa-cargo", "consulta-pqr")
TEST_SHARE = 0.3  # de las frases base de cada (comando, idioma) van al conjunto cerrado
AGENTS = ("disputas", "consultas", "recepcion")

# Lo que el agente acaba de decir y el nodo en que está, por agente y contexto.
_ASK: dict[str, tuple[str, str]] = {
    "disputas": ("pedir_cargo",
                 "¿Cuál es el cargo que no reconoces? Cuéntame el monto, el comercio o la fecha."),
    "consultas": ("pedir_radicado", "¿Cuál es el número de radicado de tu PQR?"),
    "recepcion": ("entender", "¿En qué te puedo ayudar?"),
}
_CONFIRM = ("confirmar", "Voy a radicar la PQR por el cargo que me indicaste. ¿Confirmas?")


def _inputs(text: str, ctx: str, agent: str) -> dict[str, JsonValue]:
    if ctx == "none":
        return {"text": text, "recent_turns": [], "current_node": None, "confirm_pending": False}
    if ctx == "confirm":
        node, question = _CONFIRM
        return {"text": text, "recent_turns": [question], "current_node": node, "confirm_pending": True}
    node, question = _ASK[agent]
    return {"text": text, "recent_turns": [question], "current_node": node, "confirm_pending": False}


@cache
def _test_texts() -> frozenset[str]:
    """Frases del conjunto cerrado (en su forma sin signos): el primer `TEST_SHARE` de cada (idioma, comando)
    por hash. Se decide por texto, así que una frase repetida en otro contexto o idioma cae del mismo lado."""
    groups: dict[tuple[str, str], dict[str, str]] = {}
    for text, _ctx, _agent, labels, lang in _rows():
        plain = _plain(text)
        groups.setdefault((lang, labels["command"]), {})[sha256_hex(plain.encode())] = plain
    return frozenset(
        group[digest] for group in groups.values()
        for digest in sorted(group)[:round(len(group) * TEST_SHARE)])


def _agent_for(text: str) -> str:
    """Reparto estable de los mensajes genéricos entre agentes (no cambia entre corridas)."""
    return AGENTS[int(sha256_hex(f"agent|{text}".encode())[:8], 16) % len(AGENTS)]


_PUNCT = re.compile(r"[¿?¡!.,;]")


def _plain(text: str) -> str:
    """Variante de escritura: sin signos y en minúsculas, como se teclea en un chat."""
    return re.sub(r"\s+", " ", _PUNCT.sub("", text)).strip().lower()


def _variants(text: str, lang: str) -> list[str]:
    if lang != "es":
        return [text]
    plain = _plain(text)
    return [text] if plain == text else [text, plain]


def _rows() -> list[tuple[str, str, str, dict[str, str], str]]:
    """(texto, contexto, agente, etiquetas, idioma) de las frases base."""
    rows: list[tuple[str, str, str, dict[str, str], str]] = []

    def add(text: str, ctx: str, agent: str | None, labels: dict[str, str], lang: str) -> None:
        rows.append((text, ctx, agent or _agent_for(text), labels, lang))

    for lang, flows in (("es", p.START_FLOW_ES), ("pt", p.START_FLOW_PT)):
        for flow, texts in flows.items():
            for text in texts:
                add(text, "none", None, {"command": "start_flow", "flow": flow}, lang)
    for lang, by_agent in (("es", p.CONTINUE_ES), ("pt", p.CONTINUE_PT)):
        for agent, texts in by_agent.items():
            for text in texts:
                add(text, "ask", agent, {"command": "continue"}, lang)
    for lang, affirm, deny in (("es", p.AFFIRM_ES, p.DENY_ES), ("pt", p.AFFIRM_PT, p.DENY_PT)):
        for text in affirm:
            add(text, "confirm", "disputas", {"command": "affirm"}, lang)
        for text in deny:
            add(text, "confirm", "disputas", {"command": "deny"}, lang)
    tables: list[tuple[str, dict[str, str], list[p.Row], list[p.Row]]] = [
        ("clarify", {"command": "clarify"}, p.CLARIFY_ES, p.CLARIFY_PT),
        ("cancel", {"command": "cancel"}, p.CANCEL_ES, p.CANCEL_PT),
        ("handoff", {"command": "handoff"}, p.HANDOFF_ES, p.HANDOFF_PT),
        ("out_of_scope", {"command": "out_of_scope"}, p.OUT_OF_SCOPE_ES, p.OUT_OF_SCOPE_PT),
        ("interrupt", {"command": "interrupt", "interrupt": "fraude"}, p.INTERRUPT_ES, p.INTERRUPT_PT),
    ]
    for _, labels, es_rows, pt_rows in tables:
        for lang, table in (("es", es_rows), ("pt", pt_rows)):
            for text, ctx in table:
                add(text, ctx, "disputas" if ctx == "confirm" else None, labels, lang)
    return rows


def examples(split: Split) -> list[DevExample]:
    """Ejemplos del conjunto pedido, ordenados por id. Mismo resultado en cada corrida."""
    want_test = split == "test"
    out: list[DevExample] = []
    seen: set[str] = set()
    for text, ctx, agent, labels, lang in _rows():
        if (_plain(text) in _test_texts()) is not want_test:
            continue
        for variant in _variants(text, lang):
            example_id = (f"ut-{lang}-{labels['command']}-"
                          f"{sha256_hex(f'{ctx}|{agent}|{variant}'.encode())[:10]}")
            if example_id in seen:
                continue
            seen.add(example_id)
            locale: Locale = "pt" if lang == "pt" else "es"
            out.append(DevExample(id=example_id, inputs=_inputs(variant, ctx, agent), labels=dict(labels),
                                  lang=locale, synthetic=lang != "es"))
    return sorted(out, key=lambda e: e.id)


def summary(split: Split) -> dict[str, dict[str, int]]:
    """Conteos por idioma y comando, para revisar la cobertura antes de gastar llamadas a JEV."""
    counts: dict[str, dict[str, int]] = {}
    for e in examples(split):
        by_command = counts.setdefault(e.lang, {})
        by_command[e.labels["command"]] = by_command.get(e.labels["command"], 0) + 1
    return counts

