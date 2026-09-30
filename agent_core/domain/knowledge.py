"""Conocimiento (M0 §2.12, M12 §2): páginas, referencias `ruta@snapshot#ancla`, vista y propósito.

Son tipos de valor que cruzan fronteras de módulos (M1 valida el nodo, M8 valida las citas, M12 lee y filtra),
por eso viven aquí y no en `agent_core.knowledge`. Ninguno lee hora ni azar."""

import re
from datetime import date
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from agent_core.domain.base import Locale, Model

if TYPE_CHECKING:
    from agent_core.domain.state import FactSource

Audience = Literal["public", "internal", "agent_only"]
PageStatus = Literal["draft", "approved"]
Purpose = Literal["customer_answer", "advisor_view", "agent_guidance"]
AUDIENCES: tuple[Audience, ...] = ("public", "internal", "agent_only")

# Ruta relativa de una página (`ruta` de `ruta@snapshot#ancla`): ASCII, sin `..`, `//`, `/` inicial ni final.
PagePath = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,254}$")]
Anchor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
SnapshotName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./@-]{0,127}$")]
PageSourceRef = Annotated[str, StringConstraints(min_length=1, max_length=500)]

MAX_PAGE_SOURCE_REFS = 50

_PATH = r"[A-Za-z0-9][A-Za-z0-9_./-]{0,254}"
_ANCHOR = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
_SNAPSHOT = r"[A-Za-z0-9][A-Za-z0-9_./@-]{0,127}"
_REF_RE = re.compile(rf"(?P<path>{_PATH})@(?P<snapshot>{_SNAPSHOT})(?:#(?P<anchor>{_ANCHOR}))?")
_SPEC_RE = re.compile(rf"(?P<path>{_PATH})(?:#(?P<anchor>{_ANCHOR}))?")


def check_page_path(path: str) -> str:
    """Rechaza rutas que escapan del árbol (`..`), con `//` o con `/` final. Lanza `ValueError`."""
    if path.endswith("/") or "//" in path or ".." in path.split("/"):
        raise ValueError("ruta de página no segura")
    return path


class PageRef(Model):
    """Una cita a una página: `ruta@snapshot#ancla` (el ancla es opcional)."""

    path: str
    snapshot: str
    anchor: str | None = None

    def __str__(self) -> str:
        return page_ref(self.path, self.snapshot, self.anchor)


class PageSpec(Model):
    """La forma de autoría de una página fija de un nodo `knowledge`: `ruta#ancla` (el ancla es opcional)."""

    path: str
    anchor: str | None = None


def page_ref(path: str, snapshot: str, anchor: str | None = None) -> str:
    """La cita `ruta@snapshot#ancla` (sin `#` si no hay ancla)."""
    return f"{path}@{snapshot}" + (f"#{anchor}" if anchor else "")


def parse_page_ref(text: str) -> PageRef | None:
    """`None` si `text` no es una cita a una página. Un `fact_id` (`fact-0001`) nunca lo es: no lleva `@`."""
    match = _REF_RE.fullmatch(text)
    if match is None:
        return None
    try:
        check_page_path(match["path"])
    except ValueError:
        return None
    return PageRef(path=match["path"], snapshot=match["snapshot"], anchor=match["anchor"])


def parse_page_spec(text: str) -> PageSpec:
    """`ruta#ancla` de autoría. Lanza `ValueError` si no cumple la forma o la ruta no es segura."""
    match = _SPEC_RE.fullmatch(text)
    if match is None:
        raise ValueError("página mal formada: se esperaba ruta o ruta#ancla")
    check_page_path(match["path"])
    return PageSpec(path=match["path"], anchor=match["anchor"])


class PageMeta(Model):
    """Metadatos de una página de conocimiento compatible con OKF (ADR 0015)."""

    path: PagePath
    anchor: Anchor | None = None
    snapshot: SnapshotName
    type: str = Field(min_length=1, max_length=64)
    audience: Audience
    status: PageStatus
    approved_by: str | None = Field(default=None, min_length=1)
    lang: Locale
    translation_of: PagePath | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    source_refs: list[PageSourceRef] = Field(default_factory=list, max_length=MAX_PAGE_SOURCE_REFS)

    @model_validator(mode="after")
    def _coherent(self) -> "PageMeta":
        check_page_path(self.path)
        if self.translation_of is not None:
            check_page_path(self.translation_of)
        if (self.status == "approved") != (self.approved_by is not None):
            raise ValueError("una página está aprobada si y solo si tiene approved_by")
        if self.valid_from is not None and self.valid_to is not None and self.valid_from > self.valid_to:
            raise ValueError("valid_from no puede ser posterior a valid_to")
        return self


class PageView(Model):
    """Una página (o una sección) ya proyectada a la vista `model` (M7). Se guarda en `RunState.pages`.

    `ref` es `ruta@snapshot#ancla` y coincide con `meta`. El contenido es el de la vista `model`: nunca lleva
    PII en claro, y si la página viene de una fuente externa llega envuelto como `untrusted_text`."""

    ref: str
    meta: PageMeta
    content_model: str

    @model_validator(mode="after")
    def _ref_matches_meta(self) -> "PageView":
        parsed = parse_page_ref(self.ref)
        if parsed is None or (parsed.path, parsed.snapshot, parsed.anchor) != (
                self.meta.path, self.meta.snapshot, self.meta.anchor):
            raise ValueError("ref no coincide con los metadatos de la página")
        return self

    @property
    def source(self) -> "FactSource":
        """Procedencia de la página como hecho: `FactSource{kind: knowledge, ref}`. Se deriva, no se
        guarda."""
        from agent_core.domain.state import FactSource

        return FactSource(kind="knowledge", ref=self.ref)


class PageRecord(Model):
    """Lo que un `KnowledgeSource` devuelve al leer una página: metadatos y texto completo (vista `full`).

    `content` nunca aparece en `repr` ni se serializa: solo lo proyecta M12 con las vistas de M7."""

    meta: PageMeta
    content: str = Field(repr=False, exclude=True)


class KnowledgeView(Model):
    """Qué puede leer un principal para un `purpose` (lo calcula `AuthzPort.knowledge_view`).

    El servicio de conocimiento filtra con ella y M12 vuelve a filtrar (doble filtro, ADR 0015)."""

    audiences: frozenset[Audience]
    approved_only: bool
