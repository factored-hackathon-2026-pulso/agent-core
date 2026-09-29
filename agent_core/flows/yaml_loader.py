"""Loader YAML seguro para el registro de autoría (M1 §3.10).

Resolvedores YAML 1.2 mínimos: booleanos solo `true`/`false`, sin fechas, números con decimales como
`Decimal`, claves como texto, sin claves duplicadas, sin alias, anclas ni etiquetas explícitas y un solo
documento.

El directorio del registro es entrada no confiable: solo se usa un `SafeLoader` derivado, el tamaño se
limita en bytes antes de parsear, y la profundidad, el número de nodos y la magnitud de los números
están acotados.
"""

import re
from decimal import Decimal
from typing import Any

import yaml

from agent_core.domain import JsonValue

MAX_BYTES = 1024 * 1024
MAX_DEPTH = 64
MAX_NODES = 100_000
MAX_NUMBER_CHARS = 100
MAX_EXPONENT = 1000


class YamlError(ValueError):
    """Archivo YAML que el registro no admite."""


class _Loader(yaml.SafeLoader):
    yaml_implicit_resolvers: dict[Any, Any] = {}  # noqa: RUF012 — reemplaza los de SafeLoader (YAML 1.1)

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[str, Any] = {}
        for key_node, value_node in node.value:
            line = key_node.start_mark.line + 1
            if not isinstance(key_node, yaml.ScalarNode):
                raise YamlError(f"línea {line}: una clave debe ser texto")
            key = str(key_node.value)
            if key in mapping:
                raise YamlError(f"línea {line}: clave duplicada {key!r}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _number_text(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    text = str(loader.construct_scalar(node))  # type: ignore[arg-type]
    if len(text) > MAX_NUMBER_CHARS:
        raise YamlError(f"línea {node.start_mark.line + 1}: número demasiado largo")
    return text


def _int(loader: yaml.SafeLoader, node: yaml.Node) -> int:
    return int(_number_text(loader, node))


def _decimal(loader: yaml.SafeLoader, node: yaml.Node) -> Decimal:
    value = Decimal(_number_text(loader, node))
    if abs(value.adjusted()) > MAX_EXPONENT:
        raise YamlError(f"línea {node.start_mark.line + 1}: exponente fuera de rango")
    return value


_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf"))
_Loader.add_implicit_resolver("tag:yaml.org,2002:int", re.compile(r"^[-+]?[0-9]+$"), list("-+0123456789"))
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(r"^[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$|^[-+]?[0-9]+[eE][-+]?[0-9]+$"),
    list("-+0123456789."),
)
_Loader.add_implicit_resolver("tag:yaml.org,2002:null", re.compile(r"^(?:~|null|)$"), ["~", "n", ""])
_Loader.add_constructor("tag:yaml.org,2002:int", _int)
_Loader.add_constructor("tag:yaml.org,2002:float", _decimal)

_STARTS = (yaml.MappingStartEvent, yaml.SequenceStartEvent)
_ENDS = (yaml.MappingEndEvent, yaml.SequenceEndEvent)


def _precheck(text: str) -> None:
    documents = 0
    depth = 0
    nodes = 0
    for event in yaml.parse(text, Loader=_Loader):
        if isinstance(event, yaml.DocumentStartEvent):
            documents += 1
            if documents > 1:
                raise YamlError("un archivo del registro tiene un solo documento")
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise YamlError("alias y anclas no admitidos")
        if getattr(event, "tag", None) is not None:
            raise YamlError("etiquetas explícitas no admitidas")
        if isinstance(event, (*_STARTS, yaml.ScalarEvent)):
            nodes += 1
            if nodes > MAX_NODES:
                raise YamlError(f"más de {MAX_NODES} nodos")
        if isinstance(event, _STARTS):
            depth += 1
            if depth > MAX_DEPTH:
                raise YamlError(f"anidamiento mayor a {MAX_DEPTH} niveles")
        elif isinstance(event, _ENDS):
            depth -= 1


def load_yaml(data: str | bytes) -> JsonValue:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_BYTES:
        raise YamlError("archivo mayor a 1 MiB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise YamlError("el archivo debe estar en UTF-8") from exc
    try:
        _precheck(text)
        loader = _Loader(text)
        try:
            value: JsonValue = loader.get_single_data()
        finally:
            loader.dispose()
    except YamlError:
        raise
    except (yaml.YAMLError, RecursionError, ArithmeticError, ValueError) as exc:
        raise YamlError(str(exc)) from exc
    return value
