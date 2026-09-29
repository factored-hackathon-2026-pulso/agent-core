"""Proyección de valores `full` para el handoff sobre M7.

- `audit`: lo que se persiste en el paquete (enmascarado, sin tokens reversibles).
- `for_reader`: lo que ve un lector concreto en `get`: M7 tokeniza en un vault descartable y `render`
  devuelve el valor real solo donde la política lo permite.

El vault es por llamada y no se sella: no toca `RunState.token_map`, que sigue siendo de M7/M4."""

from agent_core.domain import JsonValue, OnBehalfOf, Principal
from agent_core.ports import IdSource, KeyProvider
from agent_core.views import TokenVault, ViewService

PURPOSE = "handoff"


class Projector:
    def __init__(self, views: ViewService, keys: KeyProvider, ids: IdSource) -> None:
        self._views = views
        self._keys = keys
        self._ids = ids

    def audit(self, run_id: str, value: JsonValue, source: str) -> JsonValue:
        vault = TokenVault(run_id, self._keys, self._ids)
        return self._views.project(value, source, [], vault).audit

    def for_reader(self, run_id: str, value: JsonValue, source: str, reader: Principal,
                   obo: OnBehalfOf | None) -> JsonValue:
        vault = TokenVault(run_id, self._keys, self._ids)
        model_view = self._views.project(value, source, [], vault).model
        return self._render(model_view, vault, reader, obo)

    def _render(self, node: JsonValue, vault: TokenVault, reader: Principal,
                obo: OnBehalfOf | None) -> JsonValue:
        if isinstance(node, str):
            return self._views.render(node, vault, reader, PURPOSE, obo).text
        if isinstance(node, list):
            return [self._render(item, vault, reader, obo) for item in node]
        if isinstance(node, dict):
            return {key: self._render(item, vault, reader, obo) for key, item in node.items()}
        return node
