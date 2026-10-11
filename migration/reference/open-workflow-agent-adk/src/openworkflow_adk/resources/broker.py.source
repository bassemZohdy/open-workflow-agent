"""Pluggable event brokers and CloudEvents-compatible adapters."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol


class Broker(Protocol):
    async def publish(self, event: Mapping[str, Any]) -> None: ...

    async def consume(self, event_type: str | None = None) -> dict[str, Any]: ...


class BrokerEvent(dict[str, Any]):
    """Decoded workflow event with the original CloudEvent data retained."""

    def __init__(self, value: Mapping[str, Any], *, raw_data: Any = None) -> None:
        super().__init__(value)
        self.raw_data = raw_data


def to_cloudevent(event: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    """Wrap an OpenWorkflow event in a JSON CloudEvents 1.0 envelope."""
    if event.get("specversion") == "1.0" and "data" in event:
        return dict(event)
    value = dict(event)
    return {
        "specversion": "1.0",
        "id": str(uuid.uuid4()),
        "source": source,
        "type": str(value.get("type", "com.openworkflow.event")),
        "time": datetime.now(timezone.utc).isoformat(),
        "datacontenttype": "application/json",
        "data": value,
    }


def from_cloudevent(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the workflow event carried by a CloudEvents envelope."""
    if value.get("specversion") != "1.0" or "data" not in value:
        return BrokerEvent(value, raw_data=value.get("data"))
    data = value["data"]
    if not isinstance(data, dict):
        return BrokerEvent(
            {"type": value.get("type", "com.openworkflow.event"), "data": data},
            raw_data=data,
        )
    return BrokerEvent(data, raw_data=data)


class _CloudEventBroker:
    def __init__(self, *, source: str) -> None:
        self.source = source

    def _encode(self, event: Mapping[str, Any]) -> bytes:
        return json.dumps(to_cloudevent(event, source=self.source)).encode()

    def _decode(self, payload: bytes | str | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(payload, Mapping):
            value = payload
        else:
            value = json.loads(payload)
        return from_cloudevent(value)

    async def _consume_matching(self, next_event: Any, event_type: str | None) -> dict[str, Any]:
        while True:
            event = self._decode(await next_event())
            if event_type is None or event.get("type") == event_type:
                return event


class RedisStreamsBroker(_CloudEventBroker):
    """Redis Streams broker using ``XADD``/``XREAD``."""

    def __init__(
        self,
        url: str,
        *,
        stream: str = "workflow-events",
        source: str = "openworkflow-adk",
        client: Any | None = None,
    ) -> None:
        super().__init__(source=source)
        if client is None:
            import redis.asyncio as redis

            client = redis.from_url(url, decode_responses=False)
        self.client = client
        self.stream = stream
        self._last_id = "0-0"

    async def publish(self, event: Mapping[str, Any]) -> None:
        await self.client.xadd(self.stream, {"event": self._encode(event)})

    async def consume(self, event_type: str | None = None) -> dict[str, Any]:
        async def next_event() -> bytes:
            while True:
                batches = await self.client.xread({self.stream: self._last_id}, block=0)
                for _stream, entries in batches:
                    for entry_id, fields in entries:
                        self._last_id = (
                            entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                        )
                        payload = fields.get(b"event", fields.get("event"))
                        return payload

        return await self._consume_matching(next_event, event_type)


class KafkaBroker(_CloudEventBroker):
    """Kafka broker backed by ``aiokafka`` or injected producer/consumer."""

    def __init__(
        self,
        topic: str,
        *,
        bootstrap_servers: str = "localhost:9092",
        source: str = "openworkflow-adk",
        producer: Any | None = None,
        consumer: Any | None = None,
    ) -> None:
        super().__init__(source=source)
        self.topic = topic
        self.bootstrap_servers = bootstrap_servers
        self.producer = producer
        self.consumer = consumer
        self._started = False

    async def _ensure_clients(self) -> None:
        if self.producer is None or self.consumer is None:
            try:
                from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
            except ImportError as error:
                raise ImportError("KafkaBroker requires the 'brokers' extra") from error
            self.producer = self.producer or AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers
            )
            self.consumer = self.consumer or AIOKafkaConsumer(
                self.topic, bootstrap_servers=self.bootstrap_servers
            )
        if not self._started:
            await self.producer.start()
            await self.consumer.start()
            self._started = True

    async def publish(self, event: Mapping[str, Any]) -> None:
        await self._ensure_clients()
        await self.producer.send_and_wait(self.topic, self._encode(event))

    async def consume(self, event_type: str | None = None) -> dict[str, Any]:
        await self._ensure_clients()

        async def next_event() -> bytes:
            message = await self.consumer.getone()
            return message.value

        return await self._consume_matching(next_event, event_type)


class RabbitMQBroker(_CloudEventBroker):
    """RabbitMQ broker backed by ``aio-pika`` or injected channel/queue."""

    def __init__(
        self,
        url: str,
        *,
        queue: str = "workflow-events",
        source: str = "openworkflow-adk",
        channel: Any | None = None,
        queue_client: Any | None = None,
        message_factory: Any | None = None,
    ) -> None:
        super().__init__(source=source)
        self.url = url
        self.queue_name = queue
        self.channel = channel
        self.queue_client = queue_client
        self.message_factory = message_factory
        self._connection: Any | None = None

    async def _ensure_queue(self) -> None:
        if self.channel is not None and self.queue_client is not None:
            return
        try:
            import aio_pika
        except ImportError as error:
            raise ImportError("RabbitMQBroker requires the 'brokers' extra") from error
        self._connection = await aio_pika.connect_robust(self.url)
        self.channel = await self._connection.channel()
        self.queue_client = await self.channel.declare_queue(self.queue_name, durable=True)

    async def publish(self, event: Mapping[str, Any]) -> None:
        await self._ensure_queue()
        if self.message_factory is None:
            try:
                import aio_pika
            except ImportError as error:
                raise ImportError("RabbitMQBroker requires the 'brokers' extra") from error
            self.message_factory = aio_pika.Message
        await self.channel.default_exchange.publish(
            self.message_factory(body=self._encode(event)), routing_key=self.queue_name
        )

    async def consume(self, event_type: str | None = None) -> dict[str, Any]:
        await self._ensure_queue()

        async def next_event() -> bytes:
            message = await self.queue_client.get()
            async with message.process():
                return message.body

        return await self._consume_matching(next_event, event_type)


class NatsBroker(_CloudEventBroker):
    """NATS broker backed by ``nats-py`` or an injected client/subscription."""

    def __init__(
        self,
        subject: str,
        *,
        servers: str = "nats://127.0.0.1:4222",
        source: str = "openworkflow-adk",
        client: Any | None = None,
        subscription: Any | None = None,
    ) -> None:
        super().__init__(source=source)
        self.subject = subject
        self.servers = servers
        self.client = client
        self.subscription = subscription

    async def _ensure_subscription(self) -> None:
        if self.client is None:
            try:
                import nats
            except ImportError as error:
                raise ImportError("NatsBroker requires the 'brokers' extra") from error
            self.client = await nats.connect(servers=self.servers)
        if self.subscription is None:
            self.subscription = await self.client.subscribe(self.subject)

    async def publish(self, event: Mapping[str, Any]) -> None:
        await self._ensure_subscription()
        await self.client.publish(self.subject, self._encode(event))

    async def consume(self, event_type: str | None = None) -> dict[str, Any]:
        await self._ensure_subscription()

        async def next_event() -> bytes:
            message = await self.subscription.next_msg()
            return message.data

        return await self._consume_matching(next_event, event_type)


class InMemoryBroker:
    """FIFO broker suitable for local runs and deterministic tests."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def publish(self, event: Mapping[str, Any]) -> None:
        value = event if isinstance(event, BrokerEvent) else from_cloudevent(event)
        self.events.append(value)
        await self._queue.put(value)

    async def consume(self, event_type: str | None = None) -> dict[str, Any]:
        while True:
            event = await self._queue.get()
            if event_type is None or event.get("type") == event_type:
                return event
