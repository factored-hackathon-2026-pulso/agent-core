"""The field classifier from published catalog files (synthetic files; no dataset)."""

import json
from pathlib import Path

import pytest

from agent_core.composition.classification import FIELD_CLASSIFICATION_FILES_ENV, from_env, load_catalog
from agent_core.domain import SchemaError

OVERLAY = Path(__file__).parents[2] / "scripts" / "e2e" / "field-overlay.json"


def _write(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_files_merge_in_order_and_later_ones_win(tmp_path: Path) -> None:
    first = _write(tmp_path / "a.json", {"customer_products.current_balance": {"field_class": "financial"},
                                          "x": {"field_class": "public"}})
    second = _write(tmp_path / "b.json", {"x": {"field_class": "untrusted_text"}})

    classifier = from_env({FIELD_CLASSIFICATION_FILES_ENV: f"{first}, {second}"})

    assert classifier.classify("customer_products.current_balance") == "financial"
    assert classifier.classify("x") == "untrusted_text"
    assert classifier.classify("customer_products.nunca_clasificado") == "pii_direct"  # the safe side


def test_a_tagged_pii_rule_keeps_its_tag(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.json", {"address": {"field_class": "pii_direct", "tag": "addr"}})

    assert load_catalog([path])["address"].tag == "addr"


def test_the_shipped_overlay_is_valid_and_covers_what_the_copilot_writes() -> None:
    classifier = from_env({FIELD_CLASSIFICATION_FILES_ENV: str(OVERLAY)})

    assert classifier.classify("valor") == "financial" and classifier.classify("fecha") == "public"
    assert classifier.classify("resumen") == "untrusted_text"
    assert classifier.classify("directory.choices") == "public"


@pytest.mark.parametrize("env", [{}, {FIELD_CLASSIFICATION_FILES_ENV: " , "},
                                 {FIELD_CLASSIFICATION_FILES_ENV: "no-existe.json"}])
def test_missing_or_unreadable_catalogs_stop_the_process(env: dict[str, str]) -> None:
    with pytest.raises(SchemaError):
        from_env(env)


def test_a_malformed_catalog_is_rejected(tmp_path: Path) -> None:
    quasi_on_public = {"a": {"field_class": "public", "quasi": {"op": "drop", "width": 5}}}
    for bad in ([1, 2], {"a": {"field_class": "secreto"}}, quasi_on_public):
        with pytest.raises(SchemaError):
            load_catalog([_write(tmp_path / "bad.json", bad)])
