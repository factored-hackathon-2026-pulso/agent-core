"""Detector de PII en texto libre (M7 §3.2): documento, teléfono, email y cuenta.

Conservador a propósito: prefiere tokenizar de más (p. ej. un monto de 6+ dígitos) a dejar pasar un
identificador. Espera texto ya normalizado con NFKC (dígitos ASCII)."""

import re
from dataclasses import dataclass

# Partes acotadas: sin cotas, un texto largo sin `@` costaría O(n²).
EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}"
)
# Dígitos con separadores sueltos: 1 a 3 entre espacio, tab, punto, coma o guion (ASCII o U+2010-U+2015).
# Límite solo contra otros dígitos, para detectar "CC1023456789". Cada repetición consume un dígito y el
# separador está acotado: sin backtracking cuadrático.
_SEPARATOR_CLASS = r" \t.,\-\u2010-\u2015"
_DIGITS_RE = re.compile(rf"(?<![0-9])(\+)?([0-9](?:[{_SEPARATOR_CLASS}]{{1,3}}[0-9]|[0-9])*)")
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DASHES = "-\u2010\u2011\u2012\u2013\u2014\u2015"
_SEPARATORS = str.maketrans("", "", " \t.," + _DASHES)
_PHONE_SEPARATORS = frozenset(" \t" + _DASHES)
_TRAILING = " \t.," + _DASHES
MIN_DIGITS = 6
# Etiquetas de lo que este detector halla en un texto libre (no del `pii` de un campo sin clasificar).
DETECTOR_TAGS = frozenset({"email", "tel", "prod", "doc"})
_PHONE_DIGITS = 10
_ACCOUNT_DIGITS = 12


@dataclass(frozen=True, slots=True, repr=False)
class Hit:
    start: int
    end: int
    field: str
    tag: str
    value: str

    def __repr__(self) -> str:
        return f"Hit({self.tag}, {self.start}:{self.end})"


def _digit_hit(start: int, end: int, plus: bool, body: str) -> Hit | None:
    digits = body.translate(_SEPARATORS)
    if len(digits) < MIN_DIGITS or _ISO_DATE_RE.fullmatch(body):
        return None
    if plus or (len(digits) == _PHONE_DIGITS and any(ch in _PHONE_SEPARATORS for ch in body)):
        return Hit(start, end, "mobile_phone", "tel", ("+" if plus else "") + digits)
    if len(digits) >= _ACCOUNT_DIGITS:
        return Hit(start, end, "product_number", "prod", digits)
    return Hit(start, end, "document_number", "doc", digits)


def detect(text: str) -> list[Hit]:
    emails = [Hit(m.start(), m.end(), "email", "email", m.group(0)) for m in EMAIL_RE.finditer(text)]
    numbers: list[Hit] = []
    for match in _DIGITS_RE.finditer(text):
        start, end, body = match.start(), match.end(), match.group(2)
        overlapping = [e for e in emails if e.start < end and start < e.end]
        if overlapping:
            cut = overlapping[0].start
            if cut <= match.start(2):
                continue  # el número empieza dentro del email: es parte de su parte local
            # Un número pegado a un email por un separador se recorta donde empieza el email, no se descarta.
            body = text[match.start(2):cut].rstrip(_TRAILING)
            end = match.start(2) + len(body)
        hit = _digit_hit(start, end, match.group(1) is not None, body)
        if hit is not None:
            numbers.append(hit)
    return sorted(emails + numbers, key=lambda hit: hit.start)


def digit_runs(text: str) -> set[str]:
    """Cada secuencia de dígitos del texto, sin separadores ni `+`."""
    return {match.group(2).translate(_SEPARATORS) for match in _DIGITS_RE.finditer(text)}
