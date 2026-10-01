"""Tipos de entidad del registry: los de M0 más `eval_suite` (spec §3.1). Codificación canónica y hash."""

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ValidationError

from agent_core.domain import ENTITY_KIND, RegistryEntity, canonical_bytes, loads, sha256_hex
from agent_core.registry.errors import IntegrityError, RegistryError, RegistryErrorCode
from agent_core.registry.models import VersionRef
from agent_core.registry.suite import EvalSuite

SUITE_KIND = "eval_suite"
AnyEntity = RegistryEntity | EvalSuite

_MODELS: Mapping[str, type[BaseModel]] = MappingProxyType(
    {kind.value: model for model, kind in ENTITY_KIND.items()} | {SUITE_KIND: EvalSuite})


def model_for(kind: str) -> type[BaseModel]:
    try:
        return _MODELS[kind]
    except KeyError:
        detail = f"tipo de entidad desconocido: {kind[:40]}"
        raise RegistryError(RegistryErrorCode.not_found, detail) from None


def entity_kind(entity: AnyEntity) -> str:
    if isinstance(entity, EvalSuite):
        return SUITE_KIND
    return ENTITY_KIND[type(entity)].value


def version_ref(entity: AnyEntity) -> VersionRef:
    return VersionRef(kind=entity_kind(entity), id=entity.id, version=entity.version)


def encode_entity(entity: AnyEntity) -> bytes:
    return canonical_bytes(entity.model_dump(mode="json", by_alias=True))


def content_hash(entity: AnyEntity) -> str:
    return sha256_hex(encode_entity(entity))


def decode_entity(kind: str, data: bytes) -> AnyEntity:
    model = model_for(kind)
    try:
        entity = model.model_validate(loads(data))
    except (ValueError, ValidationError) as exc:
        raise IntegrityError(f"contenido ilegible de tipo {kind}") from exc
    assert isinstance(entity, (EvalSuite, *ENTITY_KIND))
    return entity  # type: ignore[return-value]
