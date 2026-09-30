"""Vistas `model`/`audit`, renderer y búsqueda de PII en claro (M7 §2, §3). Única salida de `full`."""

import re
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date

from pydantic import ConfigDict, Field

from agent_core.domain import Fingerprint, JsonValue, OnBehalfOf, Principal, dumps
from agent_core.domain.base import Model
from agent_core.ports import AuthzPort, Clock, KeyProvider, KeyPurpose
from agent_core.views.classification import UNCLASSIFIED, UNTRUSTED, FieldClassifier, FieldRule, field_name
from agent_core.views.detector import EMAIL_RE, MIN_DIGITS, digit_runs
from agent_core.views.fingerprints import fingerprint
from agent_core.views.quasi import Dropped, apply_quasi
from agent_core.views.tokens import MASK, TOKEN_RE, mask, neutralize
from agent_core.views.untrusted import tokenize_free_text, wrap_untrusted
from agent_core.views.vault import TokenVault

_STRICT = ("pii_direct", "pii_quasi")
_WALKED = ("untrusted_text", "financial", "public")


class ViewsConfigError(RuntimeError):
    """Configuración de M7 inválida al arrancar (p. ej. falta una clave). Nunca se cae a hash sin clave."""


class Views(Model):
    """Las tres vistas de un dato de cliente y la huella de `full`. `full` nunca aparece en `repr`."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True, hide_input_in_errors=True)

    # `exclude=True`: `full` no sale por `model_dump()` ni `model_dump_json()`.
    full: JsonValue = Field(repr=False, exclude=True)
    model: JsonValue
    audit: JsonValue
    fingerprint: Fingerprint


class Rendered(Model):
    """Texto para un lector concreto y los tokens desconocidos (anomalías que registra quien llama)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True, hide_input_in_errors=True)

    text: str = Field(repr=False)
    unknown_tokens: list[str] = Field(default_factory=list)


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


_MIN_NEEDLE = 4
_NUMBER_SEPARATORS = str.maketrans("", "", " .-+")


def _leaves(value: JsonValue, path: str) -> Iterator[tuple[str, JsonValue]]:
    if isinstance(value, dict):
        for key, item in value.items():
            # Igual que `_walk`: un "." en la clave cuenta como "_" para clasificar.
            yield from _leaves(item, f"{path}.{key.replace('.', '_')}")
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item, path)
    else:
        yield path, value


def _digits_present(digits: str, runs: set[str]) -> bool:
    return any(run == digits or (len(run) >= MIN_DIGITS and (digits.endswith(run) or run.endswith(digits)))
               for run in runs)


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

    def tokenize_text(self, text: str, vault: TokenVault) -> str:
        """Vista `model` del texto libre del usuario: la PII pasa a tokens del run, sin envoltura."""
        return tokenize_free_text(text, vault)

    def render(self, text_model_view: str, vault: TokenVault, reader: Principal, purpose: str,
               on_behalf_of: OnBehalfOf | None = None) -> Rendered:
        """Única vía por la que un valor `full` sale del núcleo: valor real solo si la política lo permite."""
        unknown: list[str] = []

        def replace(match: re.Match[str]) -> str:
            entry = vault.lookup(match.group(0))
            if entry is None:
                unknown.append(match.group(0))
                return MASK
            if self._authz.can_read_field(reader, on_behalf_of, entry.field, purpose):
                return entry.value
            return mask(entry.value, entry.tag)

        return Rendered(text=TOKEN_RE.sub(replace, text_model_view), unknown_tokens=unknown)

    def _rule(self, path: str, value: JsonValue, untrusted: frozenset[str],
              parent: FieldRule | None) -> FieldRule | None:
        """Precedencia: pii explícita > `untrusted_fields` > herencia del contenedor > resto del catálogo.

        Dentro de un contenedor `untrusted_text`, todo string sin regla pii propia es `untrusted_text`;
        los valores no string se resuelven con su clase (sin clasificar → `pii_direct`). Dentro de un
        contenedor `financial`/`public`, toda hoja sin regla pii propia hereda esa clase (pasa)."""
        rule = self._classifier.lookup(path)
        if rule is not None and rule.field_class in _STRICT:
            return rule
        if parent is not None and parent.field_class == "untrusted_text":
            # Solo strings y contenedores heredan `untrusted_text`; el resto se resuelve con su propia clase.
            return UNTRUSTED if isinstance(value, str | dict | list) else rule
        if path in untrusted or field_name(path) in untrusted:
            return UNTRUSTED
        return parent if parent is not None else rule

    def _walk(self, value: JsonValue, path: str, ctx: _Ctx, leaf: _Leaf,
              parent: FieldRule | None = None) -> JsonValue | Dropped:
        if value is None:
            return None
        rule = self._rule(path, value, ctx.untrusted, parent)
        container = isinstance(value, dict | list)
        if container and (rule is None or rule.field_class in _WALKED):
            # Un contenedor sin clasificar, `untrusted_text`, `financial` o `public` se recorre: cada hijo
            # con regla pii explícita toma su clase y el resto hereda la del contenedor.
            if isinstance(value, dict):
                out: dict[str, JsonValue] = {}
                for key, item in value.items():
                    child = f"{path}.{key.replace('.', '_')}"
                    projected = self._walk(item, child, ctx, leaf, rule)
                    if not isinstance(projected, Dropped):
                        out[neutralize(key)] = projected
                return out
            if isinstance(value, list):
                items = [self._walk(item, path, ctx, leaf, rule) for item in value]
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

    def find_clear_pii(self, text: str, facts_full: Mapping[str, JsonValue]) -> list[str]:
        """Rutas de hechos `pii_direct` que aparecen en claro, más `pattern:email`. Nunca devuelve valores."""
        visible = unicodedata.normalize("NFKC", TOKEN_RE.sub(" ", text))
        folded = visible.casefold()
        runs = digit_runs(visible)
        found: set[str] = set()
        for name, value in facts_full.items():
            for path, leaf in _leaves(value, name):
                if leaf is None or self._classifier.rule(path).field_class != "pii_direct":
                    continue
                needle = unicodedata.normalize("NFKC", _text(leaf))
                digits = needle.translate(_NUMBER_SEPARATORS)
                if digits.isascii() and digits.isdigit() and len(digits) >= MIN_DIGITS:
                    if _digits_present(digits, runs):
                        found.add(path)
                elif len(needle) >= _MIN_NEEDLE and re.search(
                    rf"(?<!\w){re.escape(needle.casefold())}(?!\w)", folded
                ):
                    found.add(path)
        if EMAIL_RE.search(visible):
            found.add("pattern:email")
        return sorted(found)
