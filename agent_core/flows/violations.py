"""Diagnóstico de la validación estática (M1 §2)."""

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from agent_core.domain import SchemaError


class Violation(BaseModel):
    """Una violación de una regla G0 o de un chequeo por agente. Nunca lleva datos de cliente."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: str
    flow: str | None = None
    node_id: str | None = None
    path: str | None = None
    message: str

    def sort_key(self) -> tuple[str, str, str, str, str]:
        """Clave de orden total: regla, flujo, nodo, ruta y mensaje."""
        return (self.rule, self.flow or "", self.node_id or "", self.path or "", self.message)


def sort_violations(violations: Iterable[Violation]) -> list[Violation]:
    """Orden total y sin duplicados (M1 §4)."""
    return sorted(set(violations), key=Violation.sort_key)


class FlowSchemaError(SchemaError):
    """`parse_flow` no pudo construir el `Flow` (G0-01 o G0-09)."""

    def __init__(self, violations: Iterable[Violation]) -> None:
        self.violations = sort_violations(violations)
        super().__init__("; ".join(f"{v.rule} {v.path or ''}: {v.message}" for v in self.violations))
