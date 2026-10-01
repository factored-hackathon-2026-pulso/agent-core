from typing import Protocol

from agent_core.domain.knowledge import KnowledgeView, PageMeta, PageRecord


class KnowledgeSource(Protocol):
    """Fuente de páginas de conocimiento de un snapshot (ADR 0015, M12 §2). `search` no existe todavía.

    El servicio filtra con la `view` que calcula el PEP: una página fuera de ella no se devuelve, ni se lista,
    y no se distingue de una ausente. M12 vuelve a filtrar (doble filtro). Un snapshot que no es el pedido
    tampoco se devuelve. Una falla de la fuente es una excepción: M12 la trata como `source_unavailable`."""

    def capabilities(self) -> frozenset[str]: ...

    def index(self, snapshot: str, view: KnowledgeView) -> list[PageMeta]:
        """Metadatos de las páginas visibles con `view`, ordenados por ruta."""
        ...

    def read(self, path: str, snapshot: str, view: KnowledgeView) -> PageRecord | None:
        """La página completa (vista `full`), o `None` si no existe o la vista no la alcanza."""
        ...
