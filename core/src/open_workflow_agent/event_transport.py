"""Finite structured CloudEvents codec and committed embedded transport seam.

Independently written against CloudEvents 1.0.2 and the common event contract.
Selected reference: open-workflow-agent-adk resources/broker.py, Apache-2.0,
48db0cf5e1a1d3202cbbec6021736e1d89e94cd0. No reference broker code is copied.
External brokers, binary bindings and automatic remote publication are absent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from .effects import EffectIdentity
from .errors import EventValidationError, WorkflowExecutionError
from .event_store import EventArrival, EventConsumerPin, EventParticipant, EventStore
from .events import EventEnvelope
from .execution_authority import current_authority
from .expressions import validate_expression_data
from .storage import StorageConnection

SPECIFICATION_VERSION = "1.0.2"
WIRE_VERSION = "1.0"
STRUCTURED_CONTENT_TYPE = "application/cloudevents+json"
TRANSPORT_PROFILE = "owa/embedded-event-transport/v1"
MAX_EVENT_BYTES = 16_777_216
_CONTEXT = frozenset(
    {"specversion", "id", "source", "type", "time", "subject", "datacontenttype", "dataschema"}
)
_RESERVED = _CONTEXT | {"data", "data_base64"}
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})"
)


def _invalid() -> EventValidationError:
    return EventValidationError("event does not match the bounded structured JSON profile")


def _string(value: Any) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 8192:
        raise _invalid()
    if any(
        ord(char) < 32
        or 127 <= ord(char) <= 159
        or 0xD800 <= ord(char) <= 0xDFFF
        or 0xFDD0 <= ord(char) <= 0xFDEF
        or ord(char) & 0xFFFF in {0xFFFE, 0xFFFF}
        for char in value
    ):
        raise _invalid()
    return value


def _uri(value: Any, *, absolute: bool = False) -> None:
    source = _string(value)
    if (
        not source.isascii()
        or re.search(r"\s|[<>\"{}|\\^`]", source)
        or re.search(r"%(?![0-9A-Fa-f]{2})", source)
    ):
        raise _invalid()
    parsed = urlsplit(source)
    if absolute and not parsed.scheme:
        raise _invalid()
    # Context metadata never becomes a credential forwarding mechanism.
    if parsed.username is not None or parsed.password is not None:
        raise _invalid()


def _validate(value: Any) -> dict[str, Any]:
    if type(value) is not dict or not {"specversion", "id", "source", "type"} <= set(value):
        raise _invalid()
    validate_expression_data(value)
    if len(value) > 128 or value["specversion"] != WIRE_VERSION or "data_base64" in value:
        raise _invalid()
    for name in value:
        if type(name) is not str or re.fullmatch(r"[a-z0-9]{1,64}", name) is None:
            raise _invalid()
    for name in ("id", "type"):
        _string(value[name])
    _uri(value["source"])
    if "subject" in value:
        _string(value["subject"])
    if "dataschema" in value:
        _uri(value["dataschema"], absolute=True)
    if "time" in value:
        stamp = _string(value["time"])
        if _TIMESTAMP.fullmatch(stamp) is None:
            raise _invalid()
        # Leap-second timestamps are outside this selected finite profile.
        datetime.fromisoformat(stamp[:-1] + "+00:00" if stamp[-1:] in {"Z", "z"} else stamp)
    media = value.get("datacontenttype")
    if media is not None:
        _string(media)
        if media != "application/json" and not re.fullmatch(
            r"application/[a-z0-9!#$&^_.+-]+\+json", media
        ):
            raise _invalid()
    elif "datacontenttype" in value:
        raise _invalid()
    for name, item in value.items():
        if name in _RESERVED:
            continue
        if type(item) is str:
            _string(item)
        elif type(item) is bool:
            pass
        elif type(item) is int and -(2**31) <= item < 2**31:
            pass
        else:
            raise _invalid()
    return value


def _canonical(value: Any, *, maximum: int) -> bytes:
    try:
        _validate(value)
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(encoded) > maximum:
            raise _invalid()
        return encoded
    except EventValidationError:
        raise
    except Exception:
        raise _invalid() from None


def _parse(payload: bytes, *, maximum: int) -> dict[str, Any]:
    if type(payload) is not bytes or not 0 < len(payload) <= maximum:
        raise _invalid()

    def distinct(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise _invalid()
            value[key] = item
        return value

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=distinct)
        _canonical(value, maximum=maximum)
        return cast(dict[str, Any], value)
    except EventValidationError:
        raise
    except Exception:
        raise _invalid() from None


@dataclass(frozen=True, slots=True)
class CloudEventMessage:
    """Immutable exact attributes and JSON data; no actor or transport authority."""

    encoded: bytes = field(repr=False)
    digest: str

    def __post_init__(self) -> None:
        value = _parse(self.encoded, maximum=MAX_EVENT_BYTES)
        if (
            _canonical(value, maximum=MAX_EVENT_BYTES) != self.encoded
            or self.digest != hashlib.sha256(self.encoded).hexdigest()
        ):
            raise _invalid()

    def as_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.encoded))

    def properties(self) -> dict[str, Any]:
        """Project without manufacturing a common timestamp or optional data."""
        value = self.as_dict()
        if "time" not in value:
            raise EventValidationError("embedded structured ingress requires an original timestamp")
        value.pop("specversion")
        return value


class CloudEventCodec:
    def __init__(self, *, max_event_bytes: int = 1_048_576) -> None:
        if type(max_event_bytes) is not int or not 1 <= max_event_bytes <= MAX_EVENT_BYTES:
            raise ValueError("CloudEvents byte bound is invalid")
        self.max_event_bytes = max_event_bytes

    def capture(self, value: Mapping[str, Any]) -> CloudEventMessage:
        encoded = _canonical(dict(value), maximum=self.max_event_bytes)
        return CloudEventMessage(encoded, hashlib.sha256(encoded).hexdigest())

    def decode(self, payload: bytes) -> CloudEventMessage:
        return self.capture(_parse(payload, maximum=self.max_event_bytes))

    def encode(self, envelope: EventEnvelope) -> bytes:
        if not isinstance(envelope, EventEnvelope) or (
            set(envelope.extensions) & (_RESERVED - {"specversion"})
        ):
            raise _invalid()
        value = envelope.as_dict()
        if value.get("specversion", WIRE_VERSION) != WIRE_VERSION:
            raise _invalid()
        value["specversion"] = WIRE_VERSION
        return self.capture(value).encoded


@dataclass(frozen=True, slots=True)
class CommittedEventAdmission:
    """Private acknowledgement evidence bound to one retained common inbox row."""

    scope: str
    sequence: int
    message: CloudEventMessage
    receipt_digest: str


class EventTransport(Protocol):
    """Only committed common acceptance may be acknowledged to a delivery source."""

    def admit(
        self, payload: bytes, *, scope: str, participant: EventParticipant | None = None
    ) -> CommittedEventAdmission: ...


class EmbeddedEventTransport:
    """No remote producer, connection, delivery claim or publication retry exists."""

    def __init__(self, store: EventStore) -> None:
        self.store = store
        self.codec = CloudEventCodec(max_event_bytes=store.limits.max_event_bytes)
        with store.transaction():
            store.connection.execute(
                "CREATE TABLE IF NOT EXISTS event_transport_admissions ("
                "receipt_key TEXT PRIMARY KEY,scope TEXT NOT NULL,source TEXT NOT NULL,"
                "event_id TEXT NOT NULL,sequence BIGINT NOT NULL,wire_digest TEXT NOT NULL,"
                "inbox_digest TEXT NOT NULL,receipt_digest TEXT NOT NULL)"
            )

    def descriptor(self) -> dict[str, Any]:
        return {
            "profile": TRANSPORT_PROFILE,
            "id": "embedded",
            "version": "1",
            "max_event_bytes": self.codec.max_event_bytes,
        }

    def admit(
        self, payload: bytes, *, scope: str, participant: EventParticipant | None = None
    ) -> CommittedEventAdmission:
        message = self.codec.decode(payload)
        properties = message.properties()
        self.store._scope(scope)
        authority = current_authority()
        if scope != (authority.principal.identity if authority is not None else "anonymous"):
            raise EventValidationError(
                "event delivery scope requires its original trusted authority"
            )

        def retain(connection: StorageConnection, envelope: EventEnvelope) -> None:
            if participant is not None:
                participant(connection, envelope)
            if self.codec.encode(envelope) != message.encoded:
                raise WorkflowExecutionError("event admission differs from its message")
            row = connection.execute(
                "SELECT sequence,digest FROM event_inbox WHERE scope=? AND source=? AND event_id=?",
                (scope, envelope.source, envelope.id),
            ).fetchone()
            if row is None:
                raise WorkflowExecutionError("event admission is unavailable")
            key = self._receipt_key(scope, message)
            digest = self._receipt_digest(scope, row[0], message, row[1])
            previous = self._retained_receipt(key)
            expected = (scope, envelope.source, envelope.id, row[0], message.digest, row[1], digest)
            if previous is not None:
                if tuple(previous) != expected:
                    raise WorkflowExecutionError("event transport admission receipt changed")
                self.store.verify_consumer_selection_in_transaction(
                    self._consumer(scope), scope=scope, sequence=row[0], digest=row[1]
                )
                return
            count = connection.execute("SELECT COUNT(*) FROM event_transport_admissions").fetchone()
            if count[0] >= self.store.limits.max_retained_events:
                raise EventValidationError("event transport admission capacity is exhausted")
            # Retain this exact arrival through the common consumer-selection
            # mechanism. The inactive cursor does not pin unrelated future events.
            pin = self.store.consumer_pin_in_transaction(self._consumer(scope))
            self.store.pin_consumer_in_transaction(
                EventConsumerPin(
                    self._consumer(scope),
                    scope,
                    max(row[0], pin.cursor if pin else 0),
                    active=False,
                ),
                selected=(EventArrival(row[0], envelope, row[1]),),
            )
            connection.execute(
                "INSERT INTO event_transport_admissions VALUES (?,?,?,?,?,?,?,?)",
                (key, *expected),
            )

        # Inspect and admit inside the same serialized common transaction.
        # Existing unrelated inbox rows cannot acquire a retrofitted receipt;
        # deleting either half of a retained transport admission also rejects.
        with self.store.transaction():
            known = self.store.connection.execute(
                "SELECT sequence FROM event_inbox WHERE scope=? AND source=? AND event_id=?",
                (scope, properties["source"], properties["id"]),
            ).fetchone()
            proof = self.store.connection.execute(
                "SELECT sequence FROM event_transport_admissions WHERE receipt_key=?",
                (self._receipt_key(scope, message),),
            ).fetchone()
            if (known is None) != (proof is None):
                raise WorkflowExecutionError("event transport admission evidence is incomplete")
            if known is not None:
                retained = self._retained_receipt(self._receipt_key(scope, message))
                if retained is None:
                    raise WorkflowExecutionError("event transport admission evidence is incomplete")
                if retained[4] != message.digest:
                    raise EventValidationError(
                        "event identity is already bound to different content"
                    )
                self.verify(CommittedEventAdmission(scope, known[0], message, retained[-1]))
            envelope = self.store.ingest_in_transaction(
                properties, default_source=properties["source"], scope=scope, participant=retain
            )
        if self.codec.encode(envelope) != message.encoded:
            raise WorkflowExecutionError("committed event admission differs from its message")
        row = self.store.connection.execute(
            "SELECT sequence,receipt_digest FROM event_transport_admissions WHERE receipt_key=?",
            (self._receipt_key(scope, message),),
        ).fetchone()
        if row is None or type(row[0]) is not int or row[0] < 1:
            raise WorkflowExecutionError("committed event admission is unavailable")
        admission = CommittedEventAdmission(scope, row[0], message, row[1])
        self.verify(admission)
        return admission

    def verify(self, admission: CommittedEventAdmission) -> None:
        self.store._scope(admission.scope)
        if type(admission.sequence) is not int or admission.sequence < 1:
            raise EventValidationError("committed event admission is invalid")
        value = admission.message.as_dict()
        size = (
            "octet_length(envelope)"
            if self.store.connection.is_postgresql
            else "length(CAST(envelope AS BLOB))"
        )
        row = self.store.connection.execute(
            f"SELECT scope,CASE WHEN {size}<=? THEN envelope END,digest "
            "FROM event_inbox WHERE sequence=?",
            (self.codec.max_event_bytes, admission.sequence),
        ).fetchone()
        if row is None or row[0] != admission.scope or row[1] is None:
            raise WorkflowExecutionError("committed event admission is unavailable")
        envelope = self.store._decode_envelope(row[1], row[2])
        if (
            envelope.id != value["id"]
            or envelope.source != value["source"]
            or self.codec.encode(envelope) != admission.message.encoded
        ):
            raise WorkflowExecutionError("committed event admission differs from its message")
        receipt = self._retained_receipt(self._receipt_key(admission.scope, admission.message))
        digest = self._receipt_digest(
            admission.scope, admission.sequence, admission.message, row[2]
        )
        if (
            receipt is None
            or tuple(receipt)
            != (
                admission.scope,
                envelope.source,
                envelope.id,
                admission.sequence,
                admission.message.digest,
                row[2],
                digest,
            )
            or admission.receipt_digest != digest
        ):
            raise WorkflowExecutionError("committed event transport receipt is unavailable")
        self.store.verify_consumer_selection_in_transaction(
            self._consumer(admission.scope),
            scope=admission.scope,
            sequence=admission.sequence,
            digest=row[2],
        )

    def _retained_receipt(self, key: str) -> Any:
        fields = []
        for name, maximum in (
            ("scope", 1024),
            ("source", 8192),
            ("event_id", 8192),
            ("wire_digest", 64),
            ("inbox_digest", 64),
            ("receipt_digest", 64),
        ):
            size = (
                f"octet_length({name})"
                if self.store.connection.is_postgresql
                else f"length(CAST({name} AS BLOB))"
            )
            fields.append(f"CASE WHEN {size}<={maximum} THEN {name} END")
        fields.insert(3, "sequence")
        return self.store.connection.execute(
            "SELECT " + ",".join(fields) + " FROM event_transport_admissions WHERE receipt_key=?",
            (key,),
        ).fetchone()

    @staticmethod
    def _receipt_key(scope: str, message: CloudEventMessage) -> str:
        value = message.as_dict()
        return hashlib.sha256(
            json.dumps([TRANSPORT_PROFILE, scope, value["source"], value["id"]]).encode()
        ).hexdigest()

    @staticmethod
    def _receipt_digest(
        scope: str, sequence: int, message: CloudEventMessage, inbox_digest: str
    ) -> str:
        return hashlib.sha256(
            json.dumps(
                [TRANSPORT_PROFILE, scope, sequence, message.digest, inbox_digest],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    @staticmethod
    def _consumer(scope: str) -> str:
        return TRANSPORT_PROFILE + ":" + hashlib.sha256(scope.encode()).hexdigest()

    async def admit_and_ack(
        self,
        payload: bytes,
        *,
        scope: str,
        acknowledge: Callable[[CommittedEventAdmission], Awaitable[None]],
        timeout_seconds: float,
        participant: EventParticipant | None = None,
    ) -> CommittedEventAdmission:
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 60
        ):
            raise ValueError("event acknowledgement timeout is invalid")
        expires = time.monotonic() + timeout_seconds
        async with asyncio.timeout(timeout_seconds):
            admission = self.admit(payload, scope=scope, participant=participant)
            if time.monotonic() >= expires:
                raise TimeoutError
            # Lost/failed ACK never rolls back a committed inbox admission.
            # Re-delivery must verify the same source/id/content, not republish.
            await acknowledge(admission)
            return admission

    def deliver_outbox(self, *, limit: int) -> int:
        if type(limit) is not int or not 1 <= limit <= self.store.limits.scan_batch:
            raise ValueError("outbox delivery batch is invalid")
        rows = self.store.connection.execute(
            "SELECT identity FROM event_outbox WHERE delivered=0 "
            "ORDER BY created_at,identity LIMIT ?",
            (limit,),
        ).fetchall()
        for (key,) in rows:
            indexed = self.store.connection.execute(
                "SELECT payload FROM workflow_effect_invocations WHERE identity=?", (key,)
            ).fetchone()
            if indexed is None:
                raise WorkflowExecutionError("outbox publication proof is unavailable")
            identity = EffectIdentity.from_value(json.loads(indexed[0]))
            receipt = self.store.journal.get(identity)
            if identity.key != key or receipt is None or receipt.status != "completed":
                raise WorkflowExecutionError("outbox publication outcome is unresolved")
            self.store._verify_outbox(identity, receipt)
        # Existing local outbox/inbox transaction remains the delivery owner.
        # There is no remote producer and no resend of an unknown publication.
        return self.store.deliver_outbox(limit=limit)
