"""Dobles de `KnowledgeSource` (M12 §2): en memoria y sobre un árbol de archivos `knowledge/` (ADR 0015).

`FileKnowledgeSource` lee `<raíz>/<snapshot>/<ruta>`, donde cada página es un Markdown con un frontmatter YAML
compatible con OKF (`type`, `audience`, `status`, `approved_by`, `lang`, `translation_of`, `valid_from`,
`valid_to`, `source_refs`). Falla cerrado: una página sin metadatos válidos no se sirve ni se lista."""

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from agent_core.domain import JsonValue, KnowledgeView, PageMeta, PageRecord, parse_page_spec
from agent_core.flows import load_yaml

_KEYS = ("type", "audience", "status", "approved_by", "lang", "translation_of", "valid_from", "valid_to",
         "source_refs")
_FENCE = "---"
_MAX_BYTES = 1024 * 1024


def visible(meta: PageMeta, view: KnowledgeView) -> bool:
    """La regla del servicio: la audiencia está en la vista y, si la vista lo pide, la página está
    aprobada."""
    return meta.audience in view.audiences and (not view.approved_only or meta.status == "approved")


class InMemoryKnowledgeSource:
    """Páginas en memoria, indexadas por `(snapshot, ruta)`. Aplica la vista como lo haría el servicio
    real."""

    def __init__(self, records: Iterable[PageRecord] = ()) -> None:
        self._records = {(r.meta.snapshot, r.meta.path): r for r in records}

    def capabilities(self) -> frozenset[str]:
        return frozenset({"read", "index"})

    def index(self, snapshot: str, view: KnowledgeView) -> list[PageMeta]:
        found = [r.meta for (snap, _), r in self._records.items()
                 if snap == snapshot and visible(r.meta, view)]
        return sorted(found, key=lambda meta: meta.path)

    def read(self, path: str, snapshot: str, view: KnowledgeView) -> PageRecord | None:
        record = self._records.get((snapshot, path))
        return record if record is not None and visible(record.meta, view) else None


class FileKnowledgeSource:
    """Árbol de archivos `<raíz>/<snapshot>/<ruta>`. No sigue enlaces que salgan del snapshot."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def capabilities(self) -> frozenset[str]:
        return frozenset({"read", "index"})

    def index(self, snapshot: str, view: KnowledgeView) -> list[PageMeta]:
        base = self._base(snapshot)
        if base is None:
            return []
        metas: list[PageMeta] = []
        for file in sorted(base.rglob("*.md")):
            record = self._load(base, snapshot, file.relative_to(base).as_posix())
            if record is not None and visible(record.meta, view):
                metas.append(record.meta)
        return sorted(metas, key=lambda meta: meta.path)

    def read(self, path: str, snapshot: str, view: KnowledgeView) -> PageRecord | None:
        base = self._base(snapshot)
        if base is None:
            return None
        record = self._load(base, snapshot, path)
        return record if record is not None and visible(record.meta, view) else None

    def _base(self, snapshot: str) -> Path | None:
        if not snapshot or "/" in snapshot or "\\" in snapshot or snapshot.startswith("."):
            return None
        base = self._root / snapshot
        return base if base.is_dir() else None

    def _load(self, base: Path, snapshot: str, path: str) -> PageRecord | None:
        try:
            spec = parse_page_spec(path)
            if spec.anchor is not None or spec.path != path:
                return None
            file = (base / path).resolve()
            inside = file.is_relative_to(base.resolve())
            if not inside or not file.is_file() or file.stat().st_size > _MAX_BYTES:
                return None
            front, body = _split(file.read_text(encoding="utf-8"))
            meta = PageMeta.model_validate({
                "path": path, "snapshot": snapshot, **{k: front[k] for k in _KEYS if k in front}})
            return PageRecord(meta=meta, content=body)
        except (OSError, UnicodeDecodeError, ValueError, ValidationError, KeyError):
            return None


def _split(text: str) -> tuple[dict[str, Any], str]:
    """Frontmatter YAML y cuerpo. Lanza `ValueError` si no hay frontmatter o no es un mapeo."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != _FENCE:
        raise ValueError("la página no tiene frontmatter")
    for end in range(1, len(lines)):
        if lines[end].strip() == _FENCE:
            front: JsonValue = load_yaml("\n".join(lines[1:end]))
            if not isinstance(front, dict):
                raise ValueError("el frontmatter no es un mapeo")
            return front, "\n".join(lines[end + 1:])
    raise ValueError("el frontmatter no se cierra")


if TYPE_CHECKING:
    from agent_core.ports import KnowledgeSource

    def _conforms(
        a: InMemoryKnowledgeSource, b: FileKnowledgeSource,
    ) -> tuple[KnowledgeSource, KnowledgeSource]:
        return a, b
