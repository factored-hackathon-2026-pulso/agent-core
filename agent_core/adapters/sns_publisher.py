"""`EventPublisher` sobre SNS (ADR 0023). El tipo y la fuente viajan como atributos para que cada cola
suscrita filtre sin abrir el cuerpo."""

from collections.abc import Mapping
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from agent_core.ports import PublishError

if TYPE_CHECKING:
    from mypy_boto3_sns import SNSClient
    from mypy_boto3_sns.type_defs import MessageAttributeValueTypeDef


class SnsEventPublisher:
    def __init__(self, client: "SNSClient", topic_arn: str) -> None:
        self._sns = client
        self._topic = topic_arn

    def publish(self, *, event_id: str, event_type: str, body: str,
                attributes: Mapping[str, str] | None = None) -> None:
        attrs: dict[str, MessageAttributeValueTypeDef] = {
            "event_type": {"DataType": "String", "StringValue": event_type},
            "event_id": {"DataType": "String", "StringValue": event_id},
        }
        for name, value in (attributes or {}).items():
            attrs[name] = {"DataType": "String", "StringValue": value}
        try:
            self._sns.publish(TopicArn=self._topic, Message=body, MessageAttributes=attrs)
        except (ClientError, BotoCoreError) as error:  # su mensaje trae ARNs y cuentas: solo la clase
            raise PublishError(type(error).__name__) from None
