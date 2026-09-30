"""Constructor reutilizable de `ApiDeps` para pruebas de M9 que solo necesitan una app mínima."""

from agent_core.api.app import ApiDeps
from testing.builders import principal
from testing.fakes.authz import TableAuthz
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from testing.fakes.registry import InMemoryRegistry
from testing.fakes.storage import InMemoryCostCounters, InMemoryStore
from tests.m09.helpers import (
    FakeHandoffs,
    FakeTranscripts,
    FakeTurns,
    RecordingDenials,
    RecordingSecurityLog,
    StubVerifier,
)


def api_deps() -> tuple[ApiDeps, str]:
    """`ApiDeps` con dobles en memoria y la credencial de un cliente registrado."""
    store = InMemoryStore()
    verifier = StubVerifier()
    credential = verifier.register("tok-c", principal())
    deps = ApiDeps(
        verifier=verifier,
        authz=TableAuthz(),
        registry=InMemoryRegistry(),
        uow_factory=store.uow,
        counters=InMemoryCostCounters(store),
        clock=FakeClock(),
        ids=FakeIds(),
        turns=FakeTurns(),
        handoffs=FakeHandoffs(),
        transcripts=FakeTranscripts(),
        denials=RecordingDenials(),
        security=RecordingSecurityLog(),
    )
    return deps, credential
