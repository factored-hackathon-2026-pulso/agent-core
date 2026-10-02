"""Rechazo de fixtures con datos no sintéticos (M11 §3.5, decisión 13). Reporta rutas, nunca valores."""

import re
from collections.abc import Iterator
from pathlib import Path

import yaml

from agent_core.audit.replay.fixture import Fixture
from agent_core.domain import JsonValue, to_jsonable
from agent_core.domain.base import Model
from agent_core.views import FieldClassifier

_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
_DIGITS = re.compile(r"\d{6,}")
_STRICT = ("pii_direct", "pii_quasi")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class SyntheticCatalog(Model):
    email_domains: list[str]
    numbers: list[str]
    values: list[str]


class FixtureRejected(ValueError):
    def __init__(self, violations: list[str]) -> None:
        super().__init__(f"fixture con datos no sintéticos en: {', '.join(violations)}")
        self.violations = violations


def load_catalog(path: Path) -> SyntheticCatalog:
    return SyntheticCatalog.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _leaves(value: JsonValue, path: str) -> Iterator[tuple[str, JsonValue]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{path}[{index}]")
    else:
        yield path, value


def check_fixture(
    fixture: Fixture, catalog: SyntheticCatalog, classifier: FieldClassifier | None = None
) -> None:
    """Escanea `inputs`, `full` y `drafts` (los `events` van en vista `audit` y con hashes hex)."""
    classifier = classifier or FieldClassifier()
    scanned: dict[str, JsonValue] = {
        "inputs": to_jsonable(fixture.inputs),
        "full": {
            k: {"result_full": to_jsonable(v.result_full), "error": v.error, "source": v.source}
            for k, v in fixture.full.items()
        },
        "drafts": to_jsonable(fixture.drafts),
    }
    bad: list[str] = []
    for path, leaf in _leaves(scanned, "fixture"):
        if leaf is None or isinstance(leaf, bool):
            continue
        text = str(leaf)
        last = path.rsplit(".", 1)[-1].split("[")[0]
        rule = classifier.lookup(last)
        strict = rule is not None and rule.field_class in _STRICT
        if strict and text not in catalog.values:
            bad.append(path)
        elif _SHA256_HEX.fullmatch(text):
            # Exactly 64 lowercase hex characters, nothing else: a sha256 content hash (e.g. the directory
            # hash, ADR 0021), the reason `events` are not scanned either. Not personal data.
            continue
        elif any(m.group(1) not in catalog.email_domains for m in _EMAIL.finditer(text)):
            bad.append(path)
        elif any(m.group(0) not in catalog.numbers for m in _DIGITS.finditer(text)):
            bad.append(path)
    if bad:
        raise FixtureRejected(sorted(set(bad)))
