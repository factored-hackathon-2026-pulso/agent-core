"""Publicación y compatibilidad del contrato de eventos salientes v1."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from agent_core.contracts import check_contracts
from agent_core.domain import to_jsonable
from agent_core.outbound import PUBLIC_TYPES, SPEC_VERSION, OutboundEvent
from tests.outbound.test_project import _all_projected

FROZEN = Path(__file__).parent / "frozen_v1" / "OutboundEvent.json"
CONTRACTS = Path("contracts")


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_published_files_are_up_to_date() -> None:
    assert check_contracts(CONTRACTS) == []


def test_the_catalog_matches_the_code() -> None:
    catalog = _load(CONTRACTS / "events" / "catalog.json")
    assert catalog["spec_version"] == SPEC_VERSION and catalog["types"] == list(PUBLIC_TYPES)
    assert (CONTRACTS / "events" / catalog["schema"]).is_file()


def test_the_schema_has_exactly_the_public_types() -> None:
    schema = _load(CONTRACTS / "events" / "OutboundEvent.json")
    assert set(schema["discriminator"]["mapping"]) == set(PUBLIC_TYPES)


def test_every_projected_event_round_trips_through_the_published_union() -> None:
    adapter = TypeAdapter(OutboundEvent)
    for event in _all_projected():
        assert adapter.validate_python(to_jsonable(event)) == event


def test_v1_is_backward_compatible_with_the_frozen_copy() -> None:
    """Quitar o cambiar un campo, o exigir uno nuevo, rompe a los consumidores: exige `spec_version` 2."""
    frozen, current = _load(FROZEN)["$defs"], _load(CONTRACTS / "events" / "OutboundEvent.json")["$defs"]
    for name, old in frozen.items():
        assert name in current, f"se quitó {name}"
        new = current[name]
        assert {k: v for k, v in new.items() if k not in ("properties", "required")} == {
            k: v for k, v in old.items() if k not in ("properties", "required")}, f"{name} cambió de forma"
        for field, spec in old.get("properties", {}).items():
            assert new["properties"].get(field) == spec, f"{name}.{field} cambió o se quitó"
        assert set(new.get("required", [])) <= set(old.get("required", [])), f"{name} exige un campo nuevo"


def test_the_events_package_depends_only_on_domain() -> None:
    code = ("import sys, agent_core.outbound\n"
            "print([m for m in ('ports','registry','turn','handoff','audit','api','adapters','composition') "
            "if f'agent_core.{m}' in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
