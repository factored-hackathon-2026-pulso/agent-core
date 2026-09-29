"""Detector de PII en texto libre (M7 §3.2): documento, teléfono, email y cuenta.

Conservador a propósito: prefiere tokenizar de más (p. ej. un monto de 6+ dígitos) a dejar pasar un
identificador. Espera texto ya normalizado con NFKC (dígitos ASCII)."""

import re
from dataclasses import dataclass

# Partes acotadas: sin cotas, un texto largo sin `@` costaría O(n²).
EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}"
)
# Dígitos con separadores sueltos (espacio, punto, guion). Límite solo contra otros dígitos, para detectar
# "CC1023456789". Cada repetición consume un dígito: sin backtracking.
_DIGITS_RE = re.compile(r"(?<![0-9])(\+)?([0-9](?:[ .-]?[0-9])*)")
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SEPARATORS = str.maketrans("", "", " .-")
MIN_DIGITS = 6
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


def _digit_hit(match: re.Match[str]) -> Hit | None:
    body = match.group(2)
    digits = body.translate(_SEPARATORS)
    if len(digits) < MIN_DIGITS or _ISO_DATE_RE.fullmatch(body):
        return None
    plus = match.group(1) is not None
    if plus or (len(digits) == _PHONE_DIGITS and any(ch in " -" for ch in body)):
        return Hit(match.start(), match.end(), "mobile_phone", "tel", ("+" if plus else "") + digits)
    if len(digits) >= _ACCOUNT_DIGITS:
        return Hit(match.start(), match.end(), "product_number", "prod", digits)
    return Hit(match.start(), match.end(), "document_number", "doc", digits)


def detect(text: str) -> list[Hit]:
    emails = [Hit(m.start(), m.end(), "email", "email", m.group(0)) for m in EMAIL_RE.finditer(text)]
    numbers: list[Hit] = []
    for match in _DIGITS_RE.finditer(text):
        if any(e.start < match.end() and match.start() < e.end for e in emails):
            continue
        hit = _digit_hit(match)
        if hit is not None:
            numbers.append(hit)
    return sorted(emails + numbers, key=lambda hit: hit.start)


def digit_runs(text: str) -> set[str]:
    """Cada secuencia de dígitos del texto, sin separadores ni `+`."""
    return {match.group(2).translate(_SEPARATORS) for match in _DIGITS_RE.finditer(text)}
