"""`token_map` del run (M7 §3.4): token estable por (campo, valor NFC). Nunca sale del núcleo."""

import unicodedata
from dataclasses import dataclass

from agent_core.ports import IdSource, KeyProvider
from agent_core.views.tokens import TOKEN_RE, format_token


class TokenMapError(Exception):
    """El `token_map` no se pudo abrir (clave, run o contenido). Su mensaje nunca incluye valores."""


@dataclass(frozen=True, slots=True, repr=False)
class TokenEntry:
    token: str
    tag: str
    field: str
    value: str

    def __repr__(self) -> str:
        return f"TokenEntry({self.token}, field={self.field})"


class TokenVault:
    """Uno por run. Su `repr` no muestra valores."""

    def __init__(self, run_id: str, keys: KeyProvider, ids: IdSource) -> None:
        self._run_id = run_id
        self._keys = keys
        self._ids = ids
        self._by_token: dict[str, TokenEntry] = {}
        self._by_value: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}

    def tokenize(self, value: str, field: str, tag: str) -> str:
        normalized = unicodedata.normalize("NFC", value)
        existing = self._by_value.get((field, normalized))
        if existing is not None:
            return existing
        token = format_token(tag, self._counters.get(tag, 0) + 1)
        self._add(tag, field, normalized, token)
        return token

    def resolve(self, token: str) -> str | None:
        entry = self._by_token.get(token)
        return None if entry is None else entry.value

    def exists(self, token: str) -> bool:
        return token in self._by_token

    def lookup(self, token: str) -> TokenEntry | None:
        return self._by_token.get(token)

    def __len__(self) -> int:
        return len(self._by_token)

    def __repr__(self) -> str:
        return f"TokenVault(run={self._run_id}, tokens={len(self._by_token)})"

    def _add(self, tag: str, field: str, value: str, token: str) -> None:
        match = TOKEN_RE.fullmatch(token)
        if (match is None or match.group(1) != tag or token in self._by_token
                or (field, value) in self._by_value):
            raise TokenMapError("entrada de token_map inválida o repetida")
        self._by_token[token] = TokenEntry(token, tag, field, value)
        self._by_value[(field, value)] = token
        self._counters[tag] = max(self._counters.get(tag, 0), int(match.group(2)))
