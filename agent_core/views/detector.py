"""Detector de PII en texto libre (M7 §3.2): documento, teléfono, email y cuenta.

Conservador a propósito: prefiere tokenizar de más (p. ej. un monto de 6+ dígitos) a dejar pasar un
identificador. Normaliza ANTES de detectar (`fold`): quita caracteres invisibles y de control, lleva los
dígitos Unicode a ASCII, quita acentos, junta grupos de dígitos partidos por saltos de línea o muchos
espacios y reconstruye `@` y `.` ofuscados. La versión normalizada solo sirve para detectar; cada `Hit`
apunta al texto de entrada, de modo que se enmascara el span original completo.

Espera texto ya en NFKC (ancho completo, espacios especiales y dígitos superíndice ya convertidos)."""

import re
import unicodedata
from array import array
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache

# Partes acotadas: sin cotas, un texto largo sin `@` costaría O(n²).
EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}"
)
# Dígitos con separadores sueltos: 1 a 3 entre espacio, tab, punto, coma, guion (ASCII o U+2010-U+2015),
# `/`, `_`, `·` (U+00B7) y paréntesis. Límite solo contra otros dígitos, para detectar "CC1023456789". Cada
# repetición consume un dígito y el separador está acotado: sin backtracking cuadrático.
_SEP = r" \t.,\-‐-―/_·()"
_WEAK = r"[/_·]"  # separadores "débiles": también aparecen en fechas, leyes y nombres de archivo
# Fechas con separador débil (d/m/aa, d/m/aaaa, aaaa/mm/dd): se consumen aparte, sin fusionarse con vecinos.
_DATE = (rf"[0-9]{{1,2}} ?{_WEAK} ?[0-9]{{1,2}} ?{_WEAK} ?[0-9]{{2,4}}"
         rf"|[0-9]{{4}} ?{_WEAK} ?[0-9]{{1,2}} ?{_WEAK} ?[0-9]{{1,2}}")
_DIGITS_RE = re.compile(
    rf"(?<![0-9])(?:(?P<date>{_DATE})(?![0-9])(?![{_SEP}]{{0,3}}[0-9]{{3}})(?!{_WEAK}[0-9])"
    rf"|(?P<plus>\+)?(?P<body>[0-9](?:[{_SEP}]{{1,3}}[0-9]|[0-9])*))"
)
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_WEAK_CHARS_RE = re.compile(r"[/_·()]")
_GROUPS_RE = re.compile(r"[0-9]+")
_DASHES = "-‐‑‒–—―"
_SEPARATORS = str.maketrans("", "", " \t.,/_·()" + _DASHES)
_PHONE_SEPARATORS = frozenset(" \t()" + _DASHES)
_TRAILING = " \t.,/_·()" + _DASHES
MIN_DIGITS = 6
_PHONE_DIGITS = 10
_ACCOUNT_DIGITS = 12

# Fuera de ASCII imprimible (más tab, salto de línea y retorno): ahí viven los caracteres que se normalizan.
_SPECIAL_RE = re.compile(r"[^\x20-\x7e\t\n\r]+")
# Control, formato (ancho cero…), combinantes, sin asignar, uso privado y sustitutos sueltos.
_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Mn", "Me", "Cn", "Co", "Cs"})
# Invisibles que no son Cf: rellenos hangul, braille en blanco y carácter de reemplazo de objeto.
_DROPPED_CHARS = frozenset("ᅟᅠㅤﾠ⠀￼")
# Un grupo de 3+ dígitos, un salto de línea o 4+ espacios, y otro grupo de 3+ dígitos: un solo espacio.
_GAP_RE = re.compile(r"(?:(?<=[0-9]{3})|(?<=[0-9]{3}\)))(?:[ \t]*[\r\n][ \t\r\n]*|[ \t]{4,})(?=[0-9]{3})")
# Ofuscaciones de `@` y `.`; todo acotado a 3 espacios, sin backtracking.
_BRACKET_AT_RE = re.compile(
    r"[ \t]{0,3}[(\[{<][ \t]{0,3}(?:@|at|arroba)[ \t]{0,3}[)\]}>][ \t]{0,3}", re.IGNORECASE
)
_ARROBA_RE = re.compile(r"[ \t]{1,3}arroba[ \t]{1,3}", re.IGNORECASE)
_SPACED_AT_RE = re.compile(r"[ \t]{1,3}@[ \t]{0,3}|@[ \t]{1,3}")
# `(dot)`, `[.]`, `punto`: solo entre un alfanumérico y una letra ("1234 (dot) 5678" no es un número).
_BRACKET_DOT_RE = re.compile(
    r"(?<=[A-Za-z0-9])[ \t]{0,3}[(\[{<][ \t]{0,3}(?:dot|punto|\.)[ \t]{0,3}[)\]}>][ \t]{0,3}(?=[A-Za-z])",
    re.IGNORECASE,
)
_WORD_DOT_RE = re.compile(r"(?<=[A-Za-z0-9])[ \t]{1,3}(?:dot|punto)[ \t]{1,3}(?=[A-Za-z])", re.IGNORECASE)


@dataclass(frozen=True, slots=True, repr=False)
class Hit:
    start: int
    end: int
    field: str
    tag: str
    value: str

    def __repr__(self) -> str:
        return f"Hit({self.tag}, {self.start}:{self.end})"


@dataclass(frozen=True, slots=True, repr=False)
class Folded:
    """Texto normalizado para detectar y, por carácter, el span del texto de entrada del que salió.

    `starts`/`ends` son `None` cuando la normalización no cambió nada (identidad)."""

    text: str
    starts: "array[int] | None" = None
    ends: "array[int] | None" = None

    def __repr__(self) -> str:
        return f"Folded({len(self.text)} chars)"

    def source_start(self, index: int) -> int:
        return index if self.starts is None else self.starts[index]

    def source_end(self, index: int) -> int:
        """Fin en el texto de entrada del carácter normalizado `index` (exclusivo)."""
        return index + 1 if self.ends is None else self.ends[index]


@lru_cache(maxsize=8192)
def _fold_char(char: str) -> str:
    """Un carácter no ASCII → su forma de detección: "" (se borra), un carácter, nunca más de uno."""
    category = unicodedata.category(char)
    if category in _DROPPED_CATEGORIES or char in _DROPPED_CHARS:
        return ""
    if category == "Nd":
        return str(unicodedata.decimal(char))
    if category == "Zs":
        return " "
    if category in ("Zl", "Zp"):
        return "\n"
    if char == "。":  # 。 punto ideográfico
        return "."
    if category[0] == "L":
        base = unicodedata.normalize("NFD", char)[0]
        if base.isascii() and base.isalpha():
            return base  # letra acentuada → su base: "josé@x.com" es el correo "jose@x.com"
    return char


def _map_chars(text: str) -> Folded:
    """Normaliza carácter a carácter (cada uno sale 1:1 o se borra), guardando de dónde salió cada uno."""
    out: list[str] = []
    starts = array("q")
    ends = array("q")
    cursor = 0
    for match in _SPECIAL_RE.finditer(text):
        begin, finish = match.span()
        if begin > cursor:
            out.append(text[cursor:begin])
            starts.extend(range(cursor, begin))
            ends.extend(range(cursor + 1, begin + 1))
        for index in range(begin, finish):
            folded = _fold_char(text[index])
            if folded:
                out.append(folded)
                starts.append(index)
                ends.append(index + 1)
        cursor = finish
    if cursor < len(text):
        out.append(text[cursor:])
        starts.extend(range(cursor, len(text)))
        ends.extend(range(cursor + 1, len(text) + 1))
    return Folded("".join(out), starts, ends)


def _substitute(folded: Folded, pattern: re.Pattern[str], replacement: str) -> Folded:
    """Cambia cada coincidencia por `replacement`, que cubre el span completo reemplazado."""
    text = folded.text
    matches = list(pattern.finditer(text))
    if not matches:
        return folded
    old_starts = folded.starts if folded.starts is not None else array("q", range(len(text)))
    old_ends = folded.ends if folded.ends is not None else array("q", range(1, len(text) + 1))
    out: list[str] = []
    starts = array("q")
    ends = array("q")
    cursor = 0
    for match in matches:
        begin, finish = match.span()
        out.append(text[cursor:begin])
        starts.extend(old_starts[cursor:begin])
        ends.extend(old_ends[cursor:begin])
        out.append(replacement)
        starts.append(old_starts[begin])
        ends.append(old_ends[finish - 1])
        cursor = finish
    out.append(text[cursor:])
    starts.extend(old_starts[cursor:])
    ends.extend(old_ends[cursor:])
    return Folded("".join(out), starts, ends)


def fold(text: str) -> Folded:
    """Normaliza `text` (ya en NFKC) solo para detectar; ver el docstring del módulo."""
    folded = _map_chars(text) if _SPECIAL_RE.search(text) else Folded(text)
    folded = _substitute(folded, _GAP_RE, " ")
    folded = _substitute(folded, _BRACKET_AT_RE, "@")
    folded = _substitute(folded, _ARROBA_RE, "@")
    folded = _substitute(folded, _SPACED_AT_RE, "@")
    folded = _substitute(folded, _BRACKET_DOT_RE, ".")
    return _substitute(folded, _WORD_DOT_RE, ".")


def _digit_hit(start: int, end: int, plus: bool, body: str) -> Hit | None:
    digits = body.translate(_SEPARATORS)
    if len(digits) < MIN_DIGITS or _ISO_DATE_RE.fullmatch(body):
        return None
    if plus or (len(digits) == _PHONE_DIGITS and any(ch in _PHONE_SEPARATORS for ch in body)):
        return Hit(start, end, "mobile_phone", "tel", ("+" if plus else "") + digits)
    if len(digits) >= _ACCOUNT_DIGITS:
        return Hit(start, end, "product_number", "prod", digits)
    return Hit(start, end, "document_number", "doc", digits)


def _weak_accepted(body: str) -> bool:
    """Con separadores débiles (`/ _ · ( )`) solo se acepta lo que parece un identificador: 10+ dígitos, o
    3+ grupos con 8+ dígitos (sin paréntesis): `ley 1581/2012` o `(601) 234` no se tokenizan."""
    digits = len(body.translate(_SEPARATORS))
    if digits >= _PHONE_DIGITS:
        return True
    return digits >= 8 and len(_GROUPS_RE.findall(body)) >= 3 and "(" not in body and ")" not in body


def _body_hits(start: int, body_start: int, plus: bool, body: str) -> list[Hit]:
    if _WEAK_CHARS_RE.search(body) is None or _weak_accepted(body):
        hit = _digit_hit(start, body_start + len(body), plus, body)
        return [] if hit is None else [hit]
    hits: list[Hit] = []
    for part in re.finditer(r"[^/_·()]+", body):  # rechazado: cada tramo entre separadores débiles, aparte
        chunk = part.group(0)
        lead = len(chunk) - len(chunk.lstrip(_TRAILING))
        text = chunk.strip(_TRAILING)
        begin = body_start + part.start() + lead
        hit = _digit_hit(begin, begin + len(text), False, text)
        if hit is not None:
            hits.append(hit)
    return hits


def _detect_folded(text: str) -> list[Hit]:
    emails = [Hit(m.start(), m.end(), "email", "email", m.group(0)) for m in EMAIL_RE.finditer(text)]
    email_ends = [e.end for e in emails]  # no se solapan: ordenados por inicio y por fin
    found: list[Hit] = list(emails)
    for match in _DIGITS_RE.finditer(text):
        if match.group("date") is not None:
            continue
        start, end, body = match.start(), match.end(), match.group("body")
        body_start = match.start("body")
        first = bisect_right(email_ends, start)  # primer email que termina después de que empieza el número
        if first < len(emails) and emails[first].start < end:
            cut = emails[first].start
            if cut <= body_start:
                continue  # el número empieza dentro del email: es parte de su parte local
            # Un número pegado a un email por un separador se recorta donde empieza el email, no se descarta.
            body = text[body_start:cut].rstrip(_TRAILING)
        found.extend(_body_hits(start, body_start, match.group("plus") is not None, body))
    return sorted(found, key=lambda hit: hit.start)


def detect(text: str) -> list[Hit]:
    """Hits sobre el texto normalizado, con `start`/`end` en el texto de entrada (span original completo)."""
    folded = fold(text)
    hits = _detect_folded(folded.text)
    if folded.starts is None:
        return hits
    return [Hit(folded.source_start(h.start), folded.source_end(h.end - 1), h.field, h.tag, h.value)
            for h in hits]


def digit_runs(text: str) -> set[str]:
    """Cada secuencia de dígitos del texto (normalizado), sin separadores ni `+`."""
    return {m.group("body").translate(_SEPARATORS)
            for m in _DIGITS_RE.finditer(fold(text).text) if m.group("body") is not None}


def has_email(text: str) -> bool:
    """¿Hay un email en el texto, incluidas las variantes ofuscadas que normaliza `fold`?"""
    return EMAIL_RE.search(fold(text).text) is not None
