"""`FieldClassifier` from published catalog files (ADR 0008: the catalog is data).

data-pipeline exports its FieldClassification next to every published run (`field_classification.json`) as
``{"<table>.<field>" | "<field>": {"field_class", "tag", "quasi": {"op", "width"}}}``. A deployment adds a
small overlay for the fields that are not in a dataset (the engine's own, and what its agents write). Files
are merged in order, later ones win; a field no file classifies stays `pii_direct` (the safe side).
"""

import json
import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from agent_core.domain import SchemaError
from agent_core.views import FieldClassifier, FieldRule

FIELD_CLASSIFICATION_FILES_ENV = "AGENTCORE_FIELD_CLASSIFICATION_FILES"


def load_catalog(paths: list[Path]) -> dict[str, FieldRule]:
    catalog: dict[str, FieldRule] = {}
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SchemaError(f"no se pudo leer el catálogo de clasificación {path.name}") from None
        if not isinstance(raw, dict):
            raise SchemaError(f"{path.name}: el catálogo debe ser un objeto")
        for key, rule in raw.items():
            try:
                catalog[key] = FieldRule.model_validate(rule)
            except ValidationError:
                raise SchemaError(f"{path.name}: regla inválida para {key!r}") from None
    return catalog


def from_env(env: Mapping[str, str]) -> FieldClassifier:
    paths = [Path(p.strip()) for p in (env.get(FIELD_CLASSIFICATION_FILES_ENV) or "").split(",") if p.strip()]
    if not paths:
        raise SchemaError(f"falta {FIELD_CLASSIFICATION_FILES_ENV}: archivos de catálogo separados por coma")
    return FieldClassifier(load_catalog(paths))


def field_classifier(ctx: object) -> FieldClassifier:
    """Factory for `serve --field-classifier agent_core.adapters.classification:field_classifier`."""
    return from_env(os.environ)
