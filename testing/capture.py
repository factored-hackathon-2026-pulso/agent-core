"""Captura de requests salientes para medir fugas de PII (M7 §8, T-M7-01). Solo pruebas.

La conectan `ScriptedGateway` (M8) y el adaptador de JEV (M5): cada request serializado se registra aquí y la
métrica es el número de valores `pii_direct` en claro sobre los requests capturados (objetivo 0)."""

from collections.abc import Iterable

from agent_core.domain import dumps


class RequestCapture:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def record(self, payload: object) -> None:
        self.requests.append(payload if isinstance(payload, str) else dumps(payload))

    def leaks(self, clear_values: Iterable[str]) -> list[tuple[int, str]]:
        """`(índice del request, valor)` por cada valor en claro encontrado; vacío = sin fugas."""
        values = sorted(set(clear_values))
        return [(index, value) for index, request in enumerate(self.requests) for value in values
                if value in request]
