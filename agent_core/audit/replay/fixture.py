"""Fixture de replay (M11 §3.5, decisión 14): eventos + entradas en vista `full`, solo datos sintéticos."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from agent_core.domain import AnyEvent, AuthLevel, JsonValue, ToolStatus, to_jsonable
from agent_core.domain.base import Model

_MAX_BYTES = 4 * 1024 * 1024


class FullToolResult(Model):
    status: ToolStatus
    result_full: JsonValue = None
    source: str | None = None
    error: str | None = None
    required_level: AuthLevel | None = None


class Fixture(Model):
    name: str
    run_id: str
    release: str
    inputs: list[dict[str, JsonValue]]
    events: list[AnyEvent]
    full: dict[str, FullToolResult]
    drafts: list[JsonValue]


class _Loader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise ValueError(f"clave duplicada: {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep)


def _float_as_decimal(loader: yaml.SafeLoader, node: yaml.Node) -> Decimal:
    return Decimal(str(loader.construct_scalar(node)))  # type: ignore[arg-type]


_Loader.add_constructor("tag:yaml.org,2002:float", _float_as_decimal)


class _Dumper(yaml.SafeDumper):
    pass


def _represent_decimal(dumper: yaml.SafeDumper, value: Decimal) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:float", format(value, "f"))


_Dumper.add_representer(Decimal, _represent_decimal)


def dump_fixture(fixture: Fixture) -> str:
    return yaml.dump(to_jsonable(fixture), Dumper=_Dumper, sort_keys=False, allow_unicode=True)


def load_fixture(text: str) -> Fixture:
    if len(text.encode()) > _MAX_BYTES:
        raise ValueError("fixture demasiado grande")
    data = yaml.load(text, Loader=_Loader)  # Loader derivado de SafeLoader
    if not isinstance(data, dict):
        raise ValueError("el fixture debe ser un mapa YAML")
    return Fixture.model_validate(data)


def load_fixture_file(path: Path) -> Fixture:
    return load_fixture(path.read_text(encoding="utf-8"))
