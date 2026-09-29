from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from agent_core.domain.identity import PrincipalKey


class CostCounters(Protocol):
    """Lee lo que escribe `UnitOfWork.add_usage`."""

    def spent_today(self, principal: PrincipalKey, now: datetime) -> Decimal: ...

    def hits(self, principal: PrincipalKey, window: timedelta, now: datetime) -> int: ...
