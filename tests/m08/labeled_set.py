"""Conjunto etiquetado SINTÉTICO de respuestas ES/PT para medir falsos rechazos de `validate` (spec §8, §10).

Los textos PT son una traducción sintética del ES (ADR 0012: los datos PT reales los produce otro equipo).
Ningún dato es real: documentos inventados y hechos de juguete.
"""

from dataclasses import dataclass, field
from typing import Any

from agent_core.response.numbers import NumberFormat
from agent_core.response.types import CheckId, Draft, ValidationContext, ValidationResult
from agent_core.response.validate import validate
from tests.m07.helpers import make_vault
from tests.m08.helpers import COMMA_DOT, DOT_COMMA, make_ctx, make_fact

# fact_id -> (valor, origen)
Facts = dict[str, tuple[Any, str]]


@dataclass(frozen=True)
class LabeledCase:
    id: str
    locale: str
    text: str
    citations: list[str]
    facts: Facts
    expected_ok: bool
    expected_checks: list[CheckId] = field(default_factory=list)
    number_format: NumberFormat | None = DOT_COMMA
    allowed: set[str] | None = None  # por defecto todos los hechos
    tokens: list[tuple[str, str, str]] = field(default_factory=list)  # (valor, campo, tag); texto usa {t0}...
    clear_pii_needles: list[str] = field(default_factory=list)
    synthetic: bool = True


CARGO: Facts = {"f1": ({"monto": "1234.56", "fecha": "2026-10-03"}, "tool")}
TASA: Facts = {"f1": ({"tasa": "15.5"}, "tool")}
VENCE: Facts = {"f1": ({"vence": "2026-10-03"}, "tool")}
CONVERSION: Facts = {
    "f_usd": ({"monto_usd": "250.00"}, "tool"),
    "f_cop": ({"monto_cop": "1000000.00"}, "compute"),
}
MOVS: Facts = {"f1": ({"n": 3, "total": "450.00"}, "tool")}
PASO: Facts = {"f1": ({"paso": 2, "horas": 24}, "tool")}
INTENTOS: Facts = {"f1": ({"intentos": 2}, "tool")}
SALDO: Facts = {"f1": ({"saldo": "1234.56"}, "tool")}
NONE: Facts = {}
TOKEN = [("Ana", "first_name", "name")]

# (id, es, pt, citations, facts, number_format, tokens)
_OK: list[tuple[str, str, str, list[str], Facts, NumberFormat | None, list[tuple[str, str, str]]]] = [
    (
        "cargo-punto-coma",
        "Tu cargo por $1.234,56 fue registrado el 03/10/2026 y está en revisión.",
        "A sua cobrança de $1.234,56 foi registrada em 03/10/2026 e está em revisão.",
        ["f1"],
        CARGO,
        DOT_COMMA,
        [],
    ),
    (
        "cargo-coma-punto",
        "Tu cargo por $1,234.56 fue registrado el 03/10/2026 y está en revisión.",
        "A sua cobrança de $1,234.56 foi registrada em 03/10/2026 e está em revisão.",
        ["f1"],
        CARGO,
        COMMA_DOT,
        [],
    ),
    (
        "tasa",
        "La tasa aplicada es del 15,5% sobre el saldo pendiente de tu cuenta.",
        "A taxa aplicada é de 15,5% sobre o saldo pendente da sua conta.",
        ["f1"],
        TASA,
        DOT_COMMA,
        [],
    ),
    (
        "fecha-texto",
        "Tu solicitud vence el 3 de octubre de 2026 según nuestros registros.",
        "A sua solicitação vence em 3 de outubro de 2026 conforme os nossos registros.",
        ["f1"],
        VENCE,
        DOT_COMMA,
        [],
    ),
    (
        "conversion-compute",
        "Son 250,00 USD, que equivalen a 1.000.000,00 COP al cambio del día.",
        "São 250,00 USD, que equivalem a 1.000.000,00 COP no câmbio do dia.",
        ["f_usd", "f_cop"],
        CONVERSION,
        DOT_COMMA,
        [],
    ),
    (
        "token-conocido",
        "Hola {t0}, tu solicitud sigue en revisión y te avisaremos pronto.",
        "Olá {t0}, a sua solicitação continua em revisão e avisaremos em breve.",
        [],
        NONE,
        DOT_COMMA,
        TOKEN,
    ),
    (
        "sin-cifras",
        "Tu solicitud sigue en revisión y te avisaremos cuando haya novedades.",
        "A sua solicitação continua em revisão e avisaremos quando houver novidades.",
        [],
        NONE,
        DOT_COMMA,
        [],
    ),
    (
        "saldo-sin-formato-inequivoco",
        "Tu saldo es de $1.234,56 al día de hoy en tu cuenta corriente.",
        "O seu saldo é de $1.234,56 hoje na sua conta corrente.",
        ["f1"],
        SALDO,
        None,
        [],
    ),
    (
        "cantidad-y-total",
        "Tienes 3 movimientos por un total de 450,00 en el periodo consultado.",
        "Você tem 3 movimentos com um total de 450,00 no período consultado.",
        ["f1"],
        MOVS,
        DOT_COMMA,
        [],
    ),
    (
        "paso-y-horas-con-hecho",
        "Ahora sigue el paso 2 de tu trámite, y tomará 24 horas hábiles completar.",
        "Agora segue o passo 2 do seu processo, e levará 24 horas úteis para concluir.",
        ["f1"],
        PASO,
        DOT_COMMA,
        [],
    ),
    (
        "entero-con-hecho",
        "El número de intentos restantes es 2 antes de que se bloquee tu tarjeta.",
        "O número de tentativas restantes é 2 antes que o seu cartão seja bloqueado.",
        ["f1"],
        INTENTOS,
        DOT_COMMA,
        [],
    ),
    (
        "cargo-y-tasa-juntos",
        "Tu cargo por $1.234,56 tiene una tasa del 15,5% según los datos del sistema.",
        "A sua cobrança de $1.234,56 tem uma taxa de 15,5% segundo os dados do sistema.",
        ["f1", "f2"],
        {**CARGO, "f2": ({"tasa": "15.5"}, "tool")},
        DOT_COMMA,
        [],
    ),
]

LABELED: list[LabeledCase] = []
for _id, _es, _pt, _cit, _facts, _fmt, _tok in _OK:
    for _loc, _text in (("es", _es), ("pt", _pt)):
        LABELED.append(
            LabeledCase(f"{_loc}-{_id}", _loc, _text, _cit, _facts, True, number_format=_fmt, tokens=_tok)
        )
LABELED += [
    LabeledCase("es-corta", "es", "Sí, claro", [], NONE, True),
    LabeledCase("pt-curta", "pt", "Sim, claro", [], NONE, True),
]

_ES_TEXT = "Tu solicitud sigue en revisión y te avisaremos cuando haya novedades."
_PT_TEXT = "A sua solicitação continua em revisão e avisaremos quando houver novidades."
LABELED += [  # respuestas incorrectas: una por comprobación y combinaciones
    LabeledCase(
        "bad-cita-no-permitida",
        "es",
        _ES_TEXT,
        ["f2"],
        {"f1": ({"a": "x"}, "tool"), "f2": ({"b": "y"}, "tool")},
        False,
        ["citations"],
        allowed={"f1"},
    ),
    LabeledCase(
        "bad-cifra-sin-fuente",
        "es",
        "Tu cargo es de $9.999,00 según nuestros registros de hoy.",
        ["f1"],
        CARGO,
        False,
        ["numbers"],
    ),
    LabeledCase(
        "bad-token-inexistente",
        "es",
        "Hola ⟦name:9⟧, tu solicitud sigue en revisión y te avisaremos pronto.",
        [],
        NONE,
        False,
        ["tokens_pii"],
    ),
    LabeledCase("bad-idioma", "es", _PT_TEXT, [], NONE, False, ["language"]),
    LabeledCase(
        "bad-ambigua-sin-formato",
        "es",
        "Tu saldo es de 1.234 pesos en tu cuenta de ahorros hoy.",
        ["f1"],
        {"f1": ({"saldo": "1234"}, "tool")},
        False,
        ["numbers"],
        number_format=None,
    ),
    LabeledCase(
        "bad-combinada",
        "es",
        "A sua cobrança de $9.999,00 continua em revisão e avisaremos em breve.",
        ["f2"],
        {"f1": ({"a": "x"}, "tool"), "f2": ({"b": "y"}, "tool")},
        False,
        ["citations", "numbers", "language"],
        allowed={"f1"},
    ),
    LabeledCase(
        "bad-pii-en-claro",
        "es",
        "Tu documento 1023456789 fue verificado y tu solicitud sigue en revisión.",
        [],
        NONE,
        False,
        ["numbers", "tokens_pii"],
        clear_pii_needles=["1023456789"],
    ),
    LabeledCase("bad-texto-vacio", "es", "   ", [], NONE, False, ["format"]),
]

# Sondeo de P2: textos correctos con números que no son cifras de negocio ni están en ningún hecho. La regla
# vigente (cuentan igual) los rechaza; se MIDE el falso rechazo, no se relaja la regla.
P2_PROBE: list[LabeledCase] = [
    LabeledCase(
        "es-paso", "es", "Ahora sigue el paso 2 de tu trámite y te avisaremos por este medio.", [], NONE, True
    ),
    LabeledCase(
        "es-horas",
        "es",
        "Recibirás una respuesta dentro de 24 horas hábiles en este mismo chat.",
        [],
        NONE,
        True,
    ),
    LabeledCase(
        "es-id-caso",
        "es",
        "Tu caso pqr-1 sigue en revisión y te avisaremos cuando haya novedades.",
        [],
        NONE,
        True,
    ),
    LabeledCase(
        "es-lista",
        "es",
        "Estos son los pasos: 1. revisa tu correo, 2. confirma tu identidad.",
        [],
        NONE,
        True,
    ),
    LabeledCase(
        "pt-passo", "pt", "Agora segue o passo 2 do seu processo e avisaremos por este meio.", [], NONE, True
    ),
    LabeledCase(
        "pt-horas",
        "pt",
        "Você receberá uma resposta dentro de 24 horas úteis neste mesmo chat.",
        [],
        NONE,
        True,
    ),
    LabeledCase(
        "pt-id-caso",
        "pt",
        "O seu caso pqr-1 continua em revisão e avisaremos quando houver novidades.",
        [],
        NONE,
        True,
    ),
    LabeledCase(
        "pt-lista",
        "pt",
        "Estes são os passos: 1. verifique o seu e-mail, 2. confirme a sua identidade.",
        [],
        NONE,
        True,
    ),
]


def build_context(case: LabeledCase) -> tuple[Draft, ValidationContext]:
    vault = make_vault()
    text = (
        case.text.format(**{f"t{i}": vault.tokenize(*tok) for i, tok in enumerate(case.tokens)})
        if case.tokens
        else case.text
    )
    facts = {fid: make_fact(fid, value, kind) for fid, (value, kind) in case.facts.items()}
    needles = case.clear_pii_needles
    ctx = make_ctx(
        facts,
        case.allowed,
        vault=vault,
        locale=case.locale,
        number_format=case.number_format,
        find_clear_pii=lambda t: ["cliente.document_number"] if any(n in t for n in needles) else [],
    )
    return Draft(text=text, citations=case.citations), ctx


def run_case(case: LabeledCase) -> ValidationResult:
    draft, ctx = build_context(case)
    return validate(draft, ctx)
