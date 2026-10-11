from types import SimpleNamespace

from openworkflow_adk.internal import (
    KafkaBroker,
    NatsBroker,
    RabbitMQBroker,
    RedisStreamsBroker,
    from_cloudevent,
    to_cloudevent,
)


def test_cloudevent_round_trip() -> None:
    envelope = to_cloudevent({"type": "demo.ready", "data": {"id": 7}}, source="test")

    assert envelope["specversion"] == "1.0"
    assert envelope["source"] == "test"
    assert from_cloudevent(envelope) == {"type": "demo.ready", "data": {"id": 7}}


class FakeRedis:
    def __init__(self) -> None:
        self.entries = []

    async def xadd(self, stream, fields):
        self.entries.append((stream, fields))
        return b"1-0"

    async def xread(self, streams, block):
        stream, last_id = next(iter(streams.items()))
        if last_id == "0-0" and self.entries:
            return [(stream.encode(), [(b"1-0", self.entries[0][1])])]
        return []


async def test_redis_streams_adapter_round_trip() -> None:
    broker = RedisStreamsBroker("redis://unused", client=FakeRedis())

    await broker.publish({"type": "demo.ready", "data": {"id": 7}})

    assert await broker.consume("demo.ready") == {"type": "demo.ready", "data": {"id": 7}}


class FakeProducer:
    def __init__(self) -> None:
        self.payload = None

    async def start(self):
        pass

    async def send_and_wait(self, topic, payload):
        self.payload = (topic, payload)


class FakeConsumer:
    def __init__(self, payload):
        self.payload = payload

    async def start(self):
        pass

    async def getone(self):
        return SimpleNamespace(value=self.payload)


async def test_kafka_adapter_uses_cloud_event_payload() -> None:
    producer = FakeProducer()
    broker = KafkaBroker("events", producer=producer, consumer=FakeConsumer(b"{}"))
    await broker.publish({"type": "demo.ready", "data": {"id": 7}})

    consumer = FakeConsumer(producer.payload[1])
    broker.consumer = consumer
    assert await broker.consume() == {"type": "demo.ready", "data": {"id": 7}}


class FakeExchange:
    def __init__(self):
        self.body = None

    async def publish(self, message, routing_key):
        self.body = message.body


class FakeChannel:
    def __init__(self):
        self.default_exchange = FakeExchange()


class FakeMessage:
    def __init__(self, body):
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def process(self):
        return self


class FakeQueue:
    def __init__(self, message):
        self.message = message

    async def get(self):
        return self.message


async def test_rabbitmq_adapter_uses_injected_transport() -> None:
    channel = FakeChannel()
    broker = RabbitMQBroker(
        "amqp://unused",
        channel=channel,
        queue_client=FakeQueue(FakeMessage(b"{}")),
        message_factory=FakeMessage,
    )
    await broker.publish({"type": "demo.ready", "data": {"id": 7}})
    broker.queue_client = FakeQueue(FakeMessage(channel.default_exchange.body))

    assert await broker.consume() == {"type": "demo.ready", "data": {"id": 7}}


class FakeNatsSubscription:
    def __init__(self, payload):
        self.payload = payload

    async def next_msg(self):
        return SimpleNamespace(data=self.payload)


class FakeNats:
    def __init__(self):
        self.payload = None

    async def publish(self, subject, payload):
        self.payload = (subject, payload)


async def test_nats_adapter_uses_injected_transport() -> None:
    client = FakeNats()
    broker = NatsBroker("events", client=client, subscription=FakeNatsSubscription(b"{}"))
    await broker.publish({"type": "demo.ready", "data": {"id": 7}})
    broker.subscription = FakeNatsSubscription(client.payload[1])

    assert await broker.consume() == {"type": "demo.ready", "data": {"id": 7}}
