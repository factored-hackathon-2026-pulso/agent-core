"""Relay del outbox: proyecta, publica, marca entregado tras la confirmación y no se frena por un fallo."""

import json
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from agent_core.adapters.sns_publisher import SnsEventPublisher
from agent_core.domain import OutboxMessage
from agent_core.ports import PublishError
from agent_core.relay import OutboxRelay
from testing.fakes.publisher import InMemoryPublisher
from testing.fakes.storage import InMemoryOutbox, InMemoryStore

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
PAYLOAD = {"handoff_ref": "handoff-0001", "run_id": "run-0001", "target_queue": "disputas",
           "priority": "high", "reason_code": "low_confidence", "language": "es",
           "reportable_attrs": {"country": "CO"}}


def _message(n: int, payload: dict[str, object] | None = None) -> OutboxMessage:
    return OutboxMessage(message_id=f"message-{n:04d}", type="handoff_created", run_id="run-0001",
                         payload=payload if payload is not None else PAYLOAD, created_at=NOW)  # type: ignore[arg-type]


def _outbox(*messages: OutboxMessage) -> InMemoryOutbox:
    store = InMemoryStore()
    for m in messages:
        store.outbox_pending[m.message_id] = m
    return InMemoryOutbox(store)


def test_it_publishes_the_public_event_and_marks_the_message_delivered() -> None:
    outbox, bus = _outbox(_message(1)), InMemoryPublisher()

    report = OutboxRelay(outbox, bus).run_once()

    assert (report.published, report.failed) == (1, 0)
    assert outbox.pending(10) == []
    sent = bus.sent[0]
    assert sent.event_id == "message-0001" and sent.event_type == "handoff.created"
    body = json.loads(sent.body)
    assert body["data"]["target_queue"] == "disputas" and body["spec_version"] == 1


def test_the_public_event_carries_no_raw_payload_fields() -> None:
    outbox, bus = _outbox(_message(1)), InMemoryPublisher()

    OutboxRelay(outbox, bus).run_once()

    assert "reportable_attrs" not in bus.sent[0].body and "country" not in bus.sent[0].body


def test_a_publish_failure_leaves_the_message_pending_for_the_next_pass() -> None:
    outbox, bus = _outbox(_message(1), _message(2)), InMemoryPublisher(fail_ids={"message-0001"})

    report = OutboxRelay(outbox, bus).run_once()

    assert (report.published, report.failed) == (1, 1)
    assert [m.message_id for m in outbox.pending(10)] == ["message-0001"]
    bus.fail_ids.clear()
    assert OutboxRelay(outbox, bus).run_once().published == 1 and outbox.pending(10) == []


def test_a_message_that_cannot_be_projected_does_not_block_the_rest() -> None:
    outbox = _outbox(_message(1, {"handoff_ref": "x"}), _message(2))
    bus = InMemoryPublisher()

    report = OutboxRelay(outbox, bus).run_once()

    assert (report.published, report.failed) == (1, 1)
    assert [e.event_id for e in bus.sent] == ["message-0002"]


def test_it_drains_more_than_one_batch() -> None:
    outbox, bus = _outbox(*[_message(n) for n in range(1, 8)]), InMemoryPublisher()

    report = OutboxRelay(outbox, bus, batch=3).run_once()

    assert report.published == 7 and outbox.pending(10) == []


def test_a_total_outage_ends_the_pass_instead_of_spinning() -> None:
    outbox, bus = _outbox(_message(1), _message(2)), InMemoryPublisher(fail_all=True)

    report = OutboxRelay(outbox, bus, batch=2).run_once()

    assert (report.published, report.failed) == (0, 2) and len(outbox.pending(10)) == 2


def test_the_batch_must_be_positive() -> None:
    with pytest.raises(ValueError):
        OutboxRelay(_outbox(), InMemoryPublisher(), batch=0)


@mock_aws
def test_the_sns_adapter_delivers_to_a_subscribed_queue_with_filterable_attributes() -> None:
    sns = boto3.client("sns", region_name="us-east-1")
    sqs = boto3.client("sqs", region_name="us-east-1")
    topic = sns.create_topic(Name="events")["TopicArn"]
    queue_url = sqs.create_queue(QueueName="handoffs")["QueueUrl"]
    attrs = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]
    queue_arn = attrs["QueueArn"]
    sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=queue_arn)
    outbox = _outbox(_message(1))

    OutboxRelay(outbox, SnsEventPublisher(sns, topic)).run_once()

    received = sqs.receive_message(QueueUrl=queue_url, MessageAttributeNames=["All"])["Messages"]
    envelope = json.loads(received[0]["Body"])
    assert json.loads(envelope["Message"])["event_id"] == "message-0001"
    assert envelope["MessageAttributes"]["event_type"]["Value"] == "handoff.created"
    assert outbox.pending(10) == []


@mock_aws
def test_the_sns_adapter_hides_provider_details_on_failure() -> None:
    sns = boto3.client("sns", region_name="us-east-1")
    publisher = SnsEventPublisher(sns, "arn:aws:sns:us-east-1:123456789012:no-existe")

    with pytest.raises(PublishError) as info:
        publisher.publish(event_id="e", event_type="t", body="{}")
    assert "123456789012" not in str(info.value)
