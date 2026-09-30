"""M12 — conocimiento (docs/specs/motor/m12-conocimiento.md). Interfaz pública.

Otros módulos (M2, M8) importan solo de aquí, nunca de los submódulos. Los tipos (`PageView`, `PageMeta`,
`KnowledgeView`, `Purpose`…) son de M0."""

from agent_core.knowledge.context import KnowledgeContext
from agent_core.knowledge.service import KnowledgeService

__all__ = ["KnowledgeContext", "KnowledgeService"]
