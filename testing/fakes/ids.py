import base64
import hashlib
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from agent_core.ports import IdKind


class FakeIds:
    """IDs deterministas: primero los sembrados (replay), luego `<kind>-0001`, `<kind>-0002`…

    Un ID nunca se repite dentro de un `kind`: si un sembrado coincide con uno secuencial, este se salta."""

    def __init__(self, seed: Mapping[IdKind, Sequence[str]] | None = None, token_seed: str = "fake") -> None:
        self._seeded: dict[IdKind, deque[str]] = {}
        self._reserved: defaultdict[IdKind, set[str]] = defaultdict(set)
        for kind, values in (seed or {}).items():
            if len(set(values)) != len(values):
                raise ValueError(f"ids sembrados repetidos para {kind.value}")
            self._seeded[kind] = deque(values)
            self._reserved[kind].update(values)
        self._counters: defaultdict[IdKind, int] = defaultdict(int)
        self._token_seed = token_seed
        self._tokens = 0

    def new_id(self, kind: IdKind) -> str:
        queue = self._seeded.get(kind)
        if queue:
            return queue.popleft()
        while True:
            self._counters[kind] += 1
            candidate = f"{kind.value}-{self._counters[kind]:04d}"
            if candidate not in self._reserved[kind]:
                self._reserved[kind].add(candidate)
                return candidate

    def secret_token(self) -> str:
        self._tokens += 1
        digest = hashlib.sha256(f"{self._token_seed}:{self._tokens}".encode()).digest()[:16]
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


if TYPE_CHECKING:
    from agent_core.ports import IdSource

    def _conforms(x: FakeIds) -> IdSource:
        return x
