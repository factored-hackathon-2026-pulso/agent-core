"""T-M7-12…15: el detector normaliza ANTES de detectar (M7 §3.2) y enmascara el span original completo.

Solo datos sintéticos: PAN de prueba públicos (Luhn válido), celular y documento inventados."""

import re
import unicodedata
from collections.abc import Callable

import pytest

from agent_core.adapters.system_clock import SystemClock
from agent_core.views.detector import detect
from tests.m07.helpers import make_service, make_vault

ZW = "​"  # ancho cero
PAN = "4111111111111111"
PHONE = "3001234567"
DOC = "1023456789"
ARABIC = "٤١١١١١١١١١١١١١١١"  # 4111111111111111 en dígitos árabe-índicos
EXT_ARABIC = "۴۱۱۱۱۱۱۱۱۱۱۱۱۱۱۱"  # ídem, árabe-índico extendido (persa)
DEVANAGARI = "४१११११११११११११११"
FULLWIDTH = "４１１１１１１１１１１１１１１１"
MATH_BOLD = "𝟒𝟏𝟏𝟏𝟏𝟏𝟏𝟏𝟏𝟏𝟏𝟏𝟏𝟏𝟏𝟏"
CIRCLED_DOC = "①⓪②③④⑤⑥⑦⑧⑨"  # NFKC: 1 0 2 3 4 5 6 7 8 9


def _model(text: str) -> tuple[str, int]:
    vault = make_vault()
    return make_service().tokenize_text(text, vault), len(vault)


def _no_digits_left(out: str, *needles: str) -> None:
    folded = "".join(ch for ch in unicodedata.normalize("NFKC", out)
                     if (ch.isdecimal() and not ch.isascii()) or ch.isdigit())
    for needle in needles:
        assert needle not in out
        assert needle not in folded
    assert not re.search(r"[٠-٩۰-۹०-९]", out)


# Cada variante: (texto con PII, fragmento que debe desaparecer por completo de la vista `model`).
PAN_VARIANTS = [
    f"tarjeta 4111{ZW}1111{ZW}1111{ZW}1111 vence",
    "tarjeta 4111/1111/1111/1111 vence",
    "tarjeta 4111_1111_1111_1111 vence",
    "tarjeta 4111 1111 1111 1111 vence",
    "tarjeta 4111-1111-1111-1111 vence",
    "tarjeta 4111.1111.1111.1111 vence",
    "tarjeta 4111 · 1111 · 1111 · 1111 vence",
    "tarjeta 4111 / 1111 / 1111 / 1111 vence",
    f"tarjeta 4{ZW}1{ZW}1{ZW}1{ZW}1{ZW}1{ZW}1{ZW}1{ZW}1{ZW}1{ZW}1{ZW}1{ZW}1{ZW}1{ZW}1{ZW}1 vence",
    "tarjeta 4111⁠ ‍1111﻿-1111­1111 vence",  # joiner, word joiner, BOM, guion blando
    "tarjeta 4111\x001111\x001111\x001111 vence",  # caracteres de control entre grupos
    f"tarjeta {ARABIC} vence",
    f"tarjeta {EXT_ARABIC} vence",
    f"tarjeta {DEVANAGARI} vence",
    f"tarjeta {FULLWIDTH} vence",
    f"tarjeta {MATH_BOLD} vence",
    f"tarjeta {ARABIC[:4]} {ARABIC[4:8]}{ZW}{ARABIC[8:12]}/{ARABIC[12:]} vence",  # árabe + mezcla
    "tarjeta ４１１１ １１１１－１１１１－１１１１ vence",  # ancho completo con guion de ancho completo
    "tarjeta 4111 1111 1111 1111 vence",  # espacios no separables, de cifra y fino
    "tarjeta 4111 ‒ 1111 ‒ 1111 ‒ 1111 vence",
    "tarjeta 4111\n1111\n1111\n1111 vence",  # un grupo por línea
    "tarjeta 4111\r\n1111\r\n1111\r\n1111 vence",
    "tarjeta 4111     1111          1111      1111 vence",  # 4+ espacios entre grupos
    "tarjeta 4111 \n 1111 \t\n  1111\n\n1111 vence",
    "tarjeta 4111\u31641111\u28001111\u20651111 vence",  # relleno hangul, braille en blanco, sin asignar
    "tarjeta 4111\u20281111\u20291111 1111 vence",  # separadores de línea y de párrafo
    "tarjeta 4111\u16801111\u16801111\u16801111 vence",  # espacio ogham
    "tarjeta 4111\U000e0020\U000e00211111\ue0001111\ufff91111 vence",  # etiquetas, uso privado, anotaciones
    "tarjeta 4111\u03011111\u03081111\u20e31111 vence",  # marcas combinantes entre grupos
    "tarjeta 4111-\n1111-\n1111-\n1111 vence",  # texto envuelto: guion al final de la línea
    "tarjeta 4111,\n1111,\n1111,\n1111 vence",
    "tarjeta 4111 -\n1111 -\n1111 -\n1111 vence",
    "tarjeta 4111\n-1111\n-1111\n-1111 vence",
    "tarjeta 4111\n(1111\n(1111\n(1111 vence",
    "tarjeta 4111-\r\n1111-\r\n1111-\r\n1111 vence",
    f"tarjeta 4111-{ZW}\n1111-{ZW}\n1111-{ZW}\n1111 vence",
    "tarjeta 4111–\n1111–\n1111–\n1111 vence",
    "tarjeta 4111 1111\n1111 1111 vence",  # separador normal en un grupo y salto en otro
]


@pytest.mark.parametrize("text", PAN_VARIANTS)
def test_t_m7_12_every_pan_variant_is_masked_in_the_model_view(text: str) -> None:
    out, tokens = _model(text)
    assert tokens == 1
    assert re.fullmatch(r"tarjeta ⟦prod:1⟧ vence", out), out
    _no_digits_left(out, "4111")


PHONE_VARIANTS = [
    "llama al (300) 123 4567 ya",
    "llama al (300)123-4567 ya",
    "llama al 300 123 4567 ya",
    f"llama al (3{ZW}00) 12{ZW}3 4567 ya",  # U+200B dentro de un grupo
    f"llama al (300) 123{ZW} 4567 ya",
    f"llama al (300){ZW}123{ZW}-{ZW}4567 ya",
    "llama al (300)\n123\n4567 ya",
    "llama al 300-\n123-\n4567 ya",
    f"llama al 300{ZW} 123{ZW} 4567 ya",
    "llama al ٣٠٠ ١٢٣ ٤٥٦٧ ya",
    "llama al ３００　１２３　４５６７ ya",
    f"llama al 3{ZW}0{ZW}0{ZW}1{ZW}2{ZW}3{ZW}4{ZW}5{ZW}6{ZW}7 ya",
]


@pytest.mark.parametrize("text", PHONE_VARIANTS)
def test_t_m7_12_every_phone_variant_is_masked(text: str) -> None:
    out, tokens = _model(text)
    assert tokens == 1
    assert re.fullmatch(r"llama al \(?⟦(tel|doc):1⟧ ya", out), out
    _no_digits_left(out, "300", "4567")


def test_t_m7_12_phone_with_country_code_and_parentheses() -> None:
    out, tokens = _model("llama al (+57) 300 123 4567 ya")
    assert tokens == 1 and out == "llama al (⟦tel:1⟧ ya"


EMAIL_VARIANTS = [
    "escribe a usuario ( @ ) dominio.com ya",
    "escribe a usuario(@)dominio.com ya",
    "escribe a usuario [at] dominio.com ya",
    "escribe a usuario [AT] dominio.com ya",
    "escribe a usuario (at) dominio.com ya",
    "escribe a usuario {at} dominio.com ya",
    "escribe a usuario [ arroba ] dominio.com ya",
    "escribe a usuario @ dominio.com ya",
    "escribe a usuario@ dominio.com ya",
    "escribe a usuario ＠ dominio.com ya",
    "escribe a usuario[at]dominio[dot]com ya",
    "escribe a usuario (at) dominio (dot) com ya",
    f"escribe a usu{ZW}ario@dom{ZW}inio.com ya",
    f"escribe a usuario{ZW}@{ZW}dominio.com ya",
    "escribe a ｕｓｕａｒｉｏ＠ｄｏｍｉｎｉｏ．ｃｏｍ ya",
    f"escribe a usuario@exam{ZW}ple.test ya",
    "escribe a usuario arroba example.test ya",
    "escribe a usuario ARROBA example punto test ya",
    "escribe a usuario [at] example.test ya",
    "escribe a usuario @ example.test ya",
    "escribe a usuario@example punto test ya",
    "escribe a usuario@example dot test ya",
    "escribe a usuario@dominio[.]com ya",
    "escribe a usuario[at]dominio[.]com ya",
    "escribe a usuario(at)dominio(.)com ya",
    "escribe a usuario@dominio{.}com ya",
    "escribe a usuario@dominio。com ya",
    "escribe a usuarió@dominio.com ya",  # letra acentuada: NFKC la compone, el detector la lleva a su base
    "escribe a usuario@dominio.cóm ya",
    "escribe a josé@dominio.com ya",
    "escribe a muñoz@dominio.com ya",
    "escribe a maría.lópez@dominio.com ya",
    "escribe a usuario@dominio\u200d。\u2060com ya",
    "escribe a søren@dominio.dk ya",  # letras que NFD no descompone
    "escribe a łukasz@dominio.pl ya",
    "escribe a ana@københavn.dk ya",
    "escribe a ana@straße.de ya",
]


@pytest.mark.parametrize("text", EMAIL_VARIANTS)
def test_t_m7_12_every_email_variant_is_masked(text: str) -> None:
    out, tokens = _model(text)
    assert tokens == 1
    assert out == "escribe a ⟦email:1⟧ ya", out


def test_t_m7_12_equivalent_variants_share_one_token() -> None:
    service, vault = make_service(), make_vault()
    outs = [service.tokenize_text(f"x {v}", vault) for v in (
        f"4111{ZW}1111{ZW}1111{ZW}1111", PAN, ARABIC, "4111/1111/1111/1111")]
    assert set(outs) == {"x ⟦prod:1⟧"} and len(vault) == 1
    assert vault.resolve("⟦prod:1⟧") == PAN
    emails = [service.tokenize_text(v, vault) for v in ("usuario@dominio.com", "usuario [at] dominio.com",
                                                        "usuario ( @ ) dominio.com")]
    assert len(set(emails)) == 1 and len(vault) == 2
    assert vault.resolve(emails[0]) == "usuario@dominio.com"


def test_t_m7_12_document_variants() -> None:
    for variant in (DOC, f"1023{ZW}456{ZW}789", "١٠٢٣٤٥٦٧٨٩", CIRCLED_DOC, "1023_456_789", "1023/456/789",
                    "102_345_678", "1023\n456\n789", "1023       456 789"):
        out, tokens = _model(f"CC {variant} ok")
        assert tokens == 1 and out.startswith("CC ⟦") and out.endswith("⟧ ok"), (variant, out)
        _no_digits_left(out, "1023", "456789")


def test_t_m7_12_masking_covers_the_whole_original_span_and_nothing_else() -> None:
    text = f"a 4111{ZW}1111{ZW}1111{ZW}1111, b {ARABIC}. c usuario ( @ ) dominio.com; d"
    out, tokens = _model(text)
    assert out == "a ⟦prod:1⟧, b ⟦prod:1⟧. c ⟦email:1⟧; d" and tokens == 2


def test_t_m7_12_hit_offsets_point_into_the_original_text() -> None:
    text = f"x 4111{ZW}1111/1111_1111 y"
    (hit,) = detect(text)
    assert text[hit.start:hit.end] == f"4111{ZW}1111/1111_1111" and hit.value == PAN
    text = "z usuario ( @ ) dominio . com"  # con punto espaciado no hay correo: nada se enmascara
    assert detect(text) == []
    text = "z usuario [at] dominio.com w"
    (hit,) = detect(text)
    assert text[hit.start:hit.end] == "usuario [at] dominio.com" and hit.value == "usuario@dominio.com"


def test_t_m7_12_zero_width_inside_text_without_pii_is_preserved() -> None:
    text = f"ho{ZW}la ‍mun{ZW}do ٣٤٥ café"
    assert _model(text) == (text, 0)


def test_t_m7_12_find_clear_pii_normalizes_text_needles_too() -> None:
    service = make_service()
    facts = {"cliente": {"first_name": "María Pérez", "address": "Calle 12 # 34-56"}}
    assert service.find_clear_pii(f"hola Mar{ZW}ía Pérez", facts) == ["cliente.first_name"]
    assert service.find_clear_pii("hola María    Pérez", facts) == ["cliente.first_name"]
    assert service.find_clear_pii(f"vive en Calle 12 #{ZW} 34-56", facts) == ["cliente.address"]
    assert service.find_clear_pii("hola Ana", facts) == []


def test_t_m7_12_find_clear_pii_finds_a_value_fused_with_neighbours() -> None:
    service = make_service()
    facts = {"cliente": {"document_number": DOC}}
    for text in (f"doc {DOC} (123)", f"{DOC}/{PHONE}", f"{DOC} · {PHONE}", f"{DOC}\n{PHONE}",
                 f"{DOC}    {PHONE}",
                 f"{DOC} {PHONE}", f"{DOC},15", f"{DOC}/12", f"{DOC}_15/09/2026"):
        assert service.find_clear_pii(text, facts) == ["cliente.document_number"], text


def test_t_m7_12_find_clear_pii_sees_the_same_variants() -> None:
    service = make_service()
    facts = {"cliente": {"document_number": DOC, "email": "usuario@dominio.com"}}
    assert service.find_clear_pii(f"doc 1023{ZW}456{ZW}789", facts) == ["cliente.document_number"]
    assert service.find_clear_pii("doc ١٠٢٣٤٥٦٧٨٩", facts) == ["cliente.document_number"]
    both = ["cliente.email", "pattern:email"]
    for variant in ("usuario [at] dominio.com", "usuario ( @ ) dominio.com", "usuario@dominio[.]com",
                    "usuario arroba dominio punto com"):
        assert service.find_clear_pii(f"mail {variant}", facts) == both
    assert service.find_clear_pii("doc 1023\n456\n789", facts) == ["cliente.document_number"]


# T-M7-13: controles negativos. Lo que no es PII no cambia, ni siquiera con variantes de formato.
NEGATIVES = [
    "pagué 500 el 12/09 y otra vez el 2026-09-12",
    "el 12/09/2026 y el 1/2/26 y el 2026/09/12 quedó listo",
    "son las 10:30 / 11:45 de la mañana",
    "cuota 12345 y código 1234",
    "versión 1.2.3 y 2.10",
    "porcentaje 12,5 % y 3/4 partes",
    "٣٤٥٦٧ no alcanza el mínimo",  # 5 dígitos árabe-índicos
    "１２３４５ no alcanza el mínimo",  # 5 dígitos de ancho completo
    f"12{ZW}345 son cinco dígitos",
    "٢٠٢٦-٠٩-١٢ es una fecha",
    "usuario @ las cinco y media",
    "te veo @ casa hoy",
    "el [at] y el (dot) son palabras sueltas",
    "usuario [at] dominio sin punto",
    "arroba ( @ ) sola",
    "(1) uno y (2) dos y (3) tres",
    "ref_12_34 y a_b_c",
    "texto normal con acentos: ñandú, camión, ¿qué tal? ¡hola!",
    "emoji 😀 y combinados 👨‍👩‍👧",  # el ZWJ de los emojis no es un separador de dígitos
    "vence 09/2026 y 03/2027 ok",
    "periodo 2026/09",
    "ley 100/1993 y Ley 1581/2012 de datos, decreto 2555/2010, resolución 123/2020",
    "radicado 2026/0912, proporción 120/80/60 y 1000/2000",
    "del 15/09/2026 - 20/09/2026 y del 01/09/2026-30/09/2026 y 12/09/2026, 13/09/2026",
    "el 15 / 09 / 2026 y el 15 /09/ 2026 y fecha 12_09_2026 y 15·09·2026",
    "31/12/1999 23:59:59 fue la hora",
    "file_2026_09_15.csv y date_2026_09_15",
    "tel (601) 234 y (555) 1234 y (123) (456) (789) y (15)/(09)/(2026)",
    "lista 1) uno 2) dos 3) tres",
    "https://ejemplo.test/items/123/456 ok",
    "10\n20\n30\n40 son cuatro líneas cortas",
    "paso 1\n\npaso 2\n\npaso 3",
    "2026-09-12  Compra  450\n2026-09-13  Retiro  300\n2026-09-14  Pago  120\n",  # extracto
    "total 450\n300 pendientes y\nHab 10\n20",
    "15/09 - 20/09 y 15/09 16/09 17/09 18/09 y 09/2026 - 12/2026 y 09/2026, 10/2026, 11/2026",
    "receta 1/2 1/3 1/4 1/5 y 2 1/2 3 1/4 4 3/4",
    "ho\u3164la \u2800mundo \ue000 café ñandú MARÍA",  # invisibles sin PII se conservan
    "",
]


@pytest.mark.parametrize("text", NEGATIVES)
def test_t_m7_13_non_pii_text_is_unchanged_and_nothing_is_tokenized(text: str) -> None:
    assert _model(text) == (unicodedata.normalize("NFKC", text), 0)
    assert detect(unicodedata.normalize("NFKC", text)) == []


def test_t_m7_13_amount_like_numbers_keep_the_conservative_behaviour() -> None:
    # Montos de 6+ dígitos siguen tokenizándose a propósito (M7 §3.2); no cambia con la normalización.
    out, tokens = _model("me cobraron $ 1.500.000 hoy")
    assert tokens == 1 and out == "me cobraron $ ⟦doc:1⟧ hoy"


def test_t_m7_13_numbers_next_to_pii_do_not_leak_into_neighbours() -> None:
    out, tokens = _model(f"tel {PHONE} y total 500 y 4111{ZW}1111{ZW}1111{ZW}1111 fin")
    assert tokens == 2 and out == "tel ⟦doc:1⟧ y total 500 y ⟦prod:1⟧ fin"


def test_t_m7_13_a_digit_long_number_that_is_not_a_card_is_still_one_masked_span() -> None:
    out, tokens = _model(f"id {'7' * 30} fin")  # 30 dígitos: no es PAN, la regla conservadora lo tokeniza
    assert tokens == 1 and out == "id ⟦prod:1⟧ fin"


# T-M7-14: rendimiento y ReDoS con entradas largas (1 MB) y hostiles.
def _timed(text: str) -> float:
    clock = SystemClock()
    start = clock.monotonic_ns()
    detect(text)
    return (clock.monotonic_ns() - start) / 1e9


MB = 1024 * 1024


@pytest.mark.parametrize("make", [
    lambda: "a" * MB,
    lambda: "texto normal, sin PII. " * (MB // 23),
    lambda: "1" + ZW * MB,
    lambda: (ZW + "1") * (MB // 2),
    lambda: "1 " * (MB // 2),
    lambda: "1/" * (MB // 2),
    lambda: "١ " * (MB // 2),
    lambda: "(" * MB,
    lambda: "[at]" * (MB // 4),
    lambda: "a ( @ ) " * (MB // 8),
    lambda: "a@b " * (MB // 4),
    lambda: "1234567 a@b.co " * (MB // 15),
    lambda: " " * MB + "@" + " " * MB,
    lambda: "(dot)" * (MB // 5) + "a",
    lambda: "x" * 60 + "@" + "a" * MB,
    lambda: "é" * MB,
    lambda: "123\n" * (MB // 4),
    lambda: "123    " * (MB // 7),
    lambda: "123-\n" * (MB // 5),
    lambda: "1 " * (MB // 4) + "\n" + "1 " * (MB // 4),
    lambda: " arroba " * (MB // 8),
    lambda: " punto " * (MB // 7),
    lambda: "_" * MB,
    lambda: "(1)" * (MB // 3),
    lambda: ("é1" * 100 + "\n") * (MB // 201),
])
def test_t_m7_14_detector_is_linear_on_one_megabyte_hostile_inputs(make: Callable[[], str]) -> None:
    assert _timed(make()) < 8.0


def test_t_m7_14_one_megabyte_of_real_pii_tokenizes_in_bounded_time() -> None:
    clock = SystemClock()
    text = f"tel {PHONE}, mail a@b.co, tarjeta 4111{ZW}1111{ZW}1111{ZW}1111. " * (MB // 70)
    service, vault = make_service(), make_vault()
    start = clock.monotonic_ns()
    out = service.tokenize_text(text, vault)
    assert (clock.monotonic_ns() - start) / 1e9 < 20.0
    assert PHONE not in out and "4111" not in out and "a@b.co" not in out
    assert len(vault) == 3


# T-M7-15: el hueco documentado por la rama del copiloto queda cerrado.
def test_t_m7_15_the_known_gap_pan_with_zero_width_separators_is_closed() -> None:
    out, _ = _model("tarjeta 4111​1111​1111​1111")
    assert "4111" not in out and "⟦prod:1⟧" in out
