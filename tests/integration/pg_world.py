"""`World` de M4 sobre Postgres real: misma harness con almacén, auditoría y outbox de Postgres."""

from typing import Any

from agent_core.adapters.postgres_uow import PostgresOutbox, PostgresStore
from agent_core.audit import AuditLog
from agent_core.domain import RunState
from agent_core.handoff import HandoffService
from agent_core.turn import TurnEngine
from testing.fakes.keys import FakeKeyProvider
from tests.m04.harness import RUN_ID, World
from tests.m04.helpers import CommitCounter
from tests.m10.helpers import HandoffAuthz, make_views


class PgWorld(World):
    def __init__(self, pg: PostgresStore, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.pg = pg
        self.store = pg  # type: ignore[assignment]  # `open_run` y `seed_at_confirm` solo usan `.uow()`
        self.uow_factory = CommitCounter(pg.uow)
        keys, authz = FakeKeyProvider.default(), HandoffAuthz()
        self.handoff = HandoffService(uow_factory=pg.uow, registry=self.registry,
                                      views=make_views(authz, keys), authz=authz, keys=keys,
                                      clock=self.clock, ids=self.ids)
        self.audit = pg.audit()  # type: ignore[assignment]
        self.chain = AuditLog(self.audit)  # type: ignore[assignment]
        self.engine = TurnEngine(**self.engine_kwargs())

    def saved(self) -> RunState:
        with self.pg.uow() as uow:
            state = uow.load_run(RUN_ID)
        assert state is not None
        return state

    def outbox(self) -> PostgresOutbox:  # type: ignore[override]
        return self.pg.outbox()
