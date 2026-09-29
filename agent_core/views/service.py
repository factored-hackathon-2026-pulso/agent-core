"""Vistas `model`/`audit`, renderer y búsqueda de PII en claro (M7 §2, §3). Única salida de `full`."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from pydantic import Field

from agent_core.domain import Fingerprint, JsonValue, dumps
from agent_core.domain.base import Model
from agent_core.ports import AuthzPort, Clock, KeyProvider, KeyPurpose
from agent_core.views.classification import UNCLASSIFIED, UNTRUSTED, FieldClassifier, FieldRule, field_name
from agent_core.views.fingerprints import fingerprint
from agent_core.views.quasi import Dropped, apply_quasi
from agent_core.views.tokens import mask, neutralize
from agent_core.views.untrusted import wrap_untrusted
from agent_core.views.vault import TokenVault

_STRICT = ("pii_direct", "pii_quasi")


class ViewsConfigError(RuntimeError):
    """Configuración de M7 inválida al arrancar (p. ej. falta una clave). Nunca se cae a hash sin clave."""


class Views(Model):
    """Las tres vistas de un dato de cliente y la huella de `full`. `full` nunca aparece en `repr`."""

    full: JsonValue = Field(repr=False)
    model: JsonValue
    audit: JsonValue
    fingerprint: Fingerprint


@dataclass(frozen=True, slots=True)
class _Ctx:
    untrusted: frozenset[str]
    vault: TokenVault
    today: date


type _Leaf = Callable[[JsonValue, str, FieldRule, _Ctx], JsonValue | Dropped]


def _text(value: JsonValue) -> str:
    return value if isinstance(value, str) else dumps(value)


def _neutral(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return neutralize(value)
    if isinstance(value, list):
        return [_neutral(item) for item in value]
    if isinstance(value, dict):
        return {neutralize(key): _neutral(item) for key, item in value.items()}
    return value


def _present(value: JsonValue | Dropped) -> JsonValue:
    return None if isinstance(value, Dropped) else value


class ViewService:
    def __init__(self, keys: KeyProvider, authz: AuthzPort, clock: Clock,
                 classifier: FieldClassifier | None = None) -> None:
        for purpose in KeyPurpose:
            try:
                keys.key(purpose, keys.current_kid(purpose))
            except KeyError:
                raise ViewsConfigError(
                    f"falta la clave vigente de {purpose.value}; nunca se cae a hash sin clave"
                ) from None
        self._keys = keys
        self._authz = authz
        self._clock = clock
        self._classifier = classifier or FieldClassifier()

    def project(self, data_full: JsonValue, source: str, untrusted_fields: list[str],
                vault: TokenVault) -> Views:
        ctx = _Ctx(frozenset(untrusted_fields), vault, self._clock.now().date())
        return Views(
            full=data_full,
            model=_present(self._walk(data_full, source, ctx, self._model_leaf)),
            audit=_present(self._walk(data_full, source, ctx, self._audit_leaf)),
            fingerprint=fingerprint(data_full, self._keys),
        )

    def _rule(self, path: str, value: JsonValue, untrusted: frozenset[str], inside: bool) -> FieldRule | None:
        """Precedencia: pii explícita > `untrusted_fields` > resto del catálogo > sin clasificar.

        Dentro de un contenedor `untrusted_text`, todo string sin regla pii propia es `untrusted_text`;
        los valores no string se resuelven con su clase (sin clasificar → `pii_direct`)."""
        rule = self._classifier.lookup(path)
        if rule is not None and rule.field_class in _STRICT:
            return rule
        if inside:
            # Solo strings y contenedores heredan `untrusted_text`; el resto se resuelve con su propia clase.
            return UNTRUSTED if isinstance(value, str | dict | list) else rule
        if path in untrusted or field_name(path) in untrusted:
            return UNTRUSTED
        return rule

    def _walk(self, value: JsonValue, path: str, ctx: _Ctx, leaf: _Leaf,
              inside: bool = False) -> JsonValue | Dropped:
        if value is None:
            return None
        rule = self._rule(path, value, ctx.untrusted, inside)
        container = isinstance(value, dict | list)
        if container and (rule is None or rule.field_class == "untrusted_text"):
            # Un contenedor sin clasificar o `untrusted_text` se recorre: cada hijo toma su propia clase.
            nested = inside or rule is not None
            if isinstance(value, dict):
                out: dict[str, JsonValue] = {}
                for key, item in value.items():
                    child = f"{path}.{key.replace('.', '_')}"
                    projected = self._walk(item, child, ctx, leaf, nested)
                    if not isinstance(projected, Dropped):
                        out[neutralize(key)] = projected
                return out
            if isinstance(value, list):
                items = [self._walk(item, path, ctx, leaf, nested) for item in value]
                return [item for item in items if not isinstance(item, Dropped)]
        return leaf(value, path, UNCLASSIFIED if rule is None else rule, ctx)

    def _model_leaf(self, value: JsonValue, path: str, rule: FieldRule, ctx: _Ctx) -> JsonValue | Dropped:
        match rule.field_class:
            case "pii_direct":
                return ctx.vault.tokenize(_text(value), field_name(path), rule.tag)
            case "pii_quasi":
                return apply_quasi(value, rule.quasi, ctx.today)
            case "untrusted_text":
                return wrap_untrusted(_text(value), path, ctx.vault)
            case _:
                return _neutral(value)

    def _audit_leaf(self, value: JsonValue, path: str, rule: FieldRule, ctx: _Ctx) -> JsonValue | Dropped:
        match rule.field_class:
            case "pii_direct":
                return mask(_text(value), rule.tag)
            case "pii_quasi":
                return apply_quasi(value, rule.quasi, ctx.today)
            case "untrusted_text":
                text = _text(value)
                fp = fingerprint(text, self._keys).model_dump()
                return {"untrusted_text": {"length": len(text), "fingerprint": fp}}
            case _:
                return value
