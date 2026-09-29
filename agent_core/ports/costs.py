from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from agent_core.domain.base import UtcDatetime
from agent_core.domain.identity import PrincipalKey


class CostCounters(Protocol):
    """Lee lo que escribe `UnitOfWork.add_usage`."""

    def spent_today(self, principal: PrincipalKey, now: UtcDatetime) -> Decimal: ...

    def hits(self, principal: PrincipalKey, window: timedelta, now: UtcDatetime) -> int: ...
