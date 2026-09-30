"""Anclas de página (m12 §3.1): una sección de Markdown es un encabezado con id explícito, `## Título
{#ancla}`.

El id es explícito (no sale del título) para que una traducción conserve el mismo ancla."""

import re

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
_ID = re.compile(r"[ \t]*\{#([A-Za-z0-9][A-Za-z0-9_-]{0,63})\}[ \t]*$")


def extract_section(content: str, anchor: str) -> str | None:
    """Texto de la sección `anchor`: desde su encabezado hasta el siguiente de igual o mayor jerarquía.

    `None` si el ancla no existe. El encabezado se devuelve sin el `{#id}`."""
    lines = content.replace("\r\n", "\n").split("\n")
    start: int | None = None
    level = 0
    out: list[str] = []
    for index, line in enumerate(lines):
        heading = _HEADING.match(line)
        if start is None:
            if heading is None:
                continue
            found = _ID.search(heading.group(2))
            if found is not None and found.group(1) == anchor:
                start, level = index, len(heading.group(1))
                out.append(f"{heading.group(1)} {_ID.sub('', heading.group(2))}")
            continue
        if heading is not None and len(heading.group(1)) <= level:
            break
        out.append(line)
    if start is None:
        return None
    return "\n".join(out).strip() + "\n"
