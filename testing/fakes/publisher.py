from collections.abc import Mapping
from dataclasses import dataclass, field

from agent_core.ports import PublishError


@dataclass
class Published:
    event_id: str
    event_type: str
    body: str
    attributes: dict[str, str]


@dataclass
class InMemoryPublisher:
    """`EventPublisher` en memoria; `fail_ids` hace fallar la publicación de esos `event_id`."""

    sent: list[Published] = field(default_factory=list)
    fail_ids: set[str] = field(default_factory=set)
    fail_all: bool = False

    def publish(self, *, event_id: str, event_type: str, body: str,
                attributes: Mapping[str, str] | None = None) -> None:
        if self.fail_all or event_id in self.fail_ids:
            raise PublishError("simulated")
        self.sent.append(Published(event_id, event_type, body, dict(attributes or {})))
