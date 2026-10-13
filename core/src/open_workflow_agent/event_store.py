"""Embedded durable event acceptance and selections, independent of engines."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from typing import Any

from ._effect_storage import insert_completed_receipt
from .effects import EffectIdentity, EffectJournal, EffectReceipt, effect_request_digest
from .errors import EventValidationError, InvocationStateConflict, OwaError, WorkflowExecutionError
from .events import EventDecision, EventEnvelope, EventProgress, EventRegistration, _make_envelope
from .expressions import validate_expression_data
from .ownership import ExecutionLease, owned_write_transaction
from .storage import StorageConnection

EventParticipant = Callable[[StorageConnection, EventEnvelope], None]
EventEvaluator = Callable[[EventEnvelope, EventProgress], EventDecision | None]


@dataclass(frozen=True, slots=True)
class EventArrival:
    sequence: int
    envelope: EventEnvelope
    digest: str


@dataclass(frozen=True, slots=True)
class EventConsumerPin:
    """Private non-invocation consumer retention cursor."""

    consumer_key: str
    scope: str
    cursor: int
    active: bool = True


@dataclass(frozen=True, slots=True)
class EventStoreLimits:
    max_event_bytes: int = 1_048_576
    max_retained_events: int = 10_000
    max_retained_bytes: int = 67_108_864
    max_subscriptions: int = 1_000
    max_outbox: int = 10_000
    scan_batch: int = 128
    retention_seconds: float = 86_400

    def __post_init__(self) -> None:
        for name in (
            "max_event_bytes",
            "max_retained_events",
            "max_retained_bytes",
            "max_subscriptions",
            "max_outbox",
            "scan_batch",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError("event storage bounds must be positive integers")
        if (
            isinstance(self.retention_seconds, bool)
            or not math.isfinite(self.retention_seconds)
            or (self.retention_seconds <= 0)
        ):
            raise ValueError("event retention must be a positive finite duration")


def _json(value: Any) -> str:
    validate_expression_data(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _identity_value(identity: EffectIdentity) -> dict[str, Any]:
    # Dataclass occurrence is immutable tuples; persistence uses finite JSON.
    return dict(json.loads(json.dumps(asdict(identity), allow_nan=False)))


def event_selection_identity(registration: EventRegistration, ordinal: int) -> EffectIdentity:
    return registration.event_identity(ordinal)


class EventStore:
    """Durable local broadcast with immutable occurrence-bound selection.

    A newly registered listener excludes arrivals selected by another listener
    of this invocation before registration. Already registered listeners retain
    their original lower bound: concurrent forks can broadcast one arrival,
    while sequential tasks and repeated loop occurrences cannot reuse it.
    Another invocation can replay retained arrivals under its own scope.
    """

    def __init__(self, journal: EffectJournal, *, limits: EventStoreLimits | None = None) -> None:
        self.journal = journal
        self.connection = journal.connection
        self.limits = limits or EventStoreLimits()
        self._lock = threading.RLock()
        self._ready = False
        statements = (
            """CREATE TABLE IF NOT EXISTS event_counters (
            counter TEXT PRIMARY KEY, value BIGINT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS event_inbox (
            sequence BIGINT PRIMARY KEY, scope TEXT NOT NULL, source TEXT NOT NULL,
            event_id TEXT NOT NULL, envelope TEXT NOT NULL, digest TEXT NOT NULL,
            submitted_digest TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
            bytes BIGINT NOT NULL, UNIQUE(scope, source, event_id))""",
            """CREATE TABLE IF NOT EXISTS event_outbox (
            identity TEXT PRIMARY KEY, invocation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            scope TEXT NOT NULL, envelope TEXT NOT NULL, digest TEXT NOT NULL,
            request_digest TEXT NOT NULL, delivered INTEGER NOT NULL,
            created_at DOUBLE PRECISION NOT NULL, bytes BIGINT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS event_subscriptions (
            identity TEXT PRIMARY KEY, invocation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            payload TEXT NOT NULL, status TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS event_selections (
            subscription TEXT NOT NULL, ordinal INTEGER NOT NULL, inbox_sequence BIGINT NOT NULL,
            digest TEXT NOT NULL, receipt_identity TEXT NOT NULL,
            PRIMARY KEY(subscription, ordinal), UNIQUE(subscription, inbox_sequence))""",
            """CREATE TABLE IF NOT EXISTS event_consumers (
            invocation_id TEXT PRIMARY KEY, cursor BIGINT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS event_invocation_profiles (
            invocation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            scope TEXT NOT NULL, profile TEXT NOT NULL, digest TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS event_consumer_pins (
            consumer_key TEXT PRIMARY KEY, scope TEXT NOT NULL,
            cursor BIGINT NOT NULL, active INTEGER NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS event_consumer_selections (
            consumer_key TEXT NOT NULL, inbox_sequence BIGINT NOT NULL, digest TEXT NOT NULL,
            PRIMARY KEY(consumer_key, inbox_sequence))""",
            "CREATE INDEX IF NOT EXISTS event_inbox_scope ON event_inbox(scope, sequence)",
            "CREATE INDEX IF NOT EXISTS event_subscription_invocation "
            "ON event_subscriptions(invocation_id)",
        )
        with self.transaction():
            for statement in statements:
                self.connection.execute(statement)
            self.connection.execute(
                "INSERT INTO event_counters(counter, value) VALUES ('arrival', 0) "
                "ON CONFLICT(counter) DO NOTHING"
            )
        self._ready = True

    def _serialize(self) -> None:
        if self._ready:
            # Embedded routing uses a single bounded admission order. This also
            # serializes capacity checks and duplicate decisions across owners.
            query = "SELECT value FROM event_counters WHERE counter = 'arrival'"
            if self.connection.is_postgresql:
                query += " FOR UPDATE"
            row = self.connection.execute(query).fetchone()
            maximum = self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM event_inbox"
            ).fetchone()[0]
            if row is None or type(row[0]) is not int or row[0] < maximum or row[0] < 0:
                raise WorkflowExecutionError("durable event arrival counter is inconsistent")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Own an unleased local transaction; participants must never commit."""
        with self._lock:
            try:
                if self.connection.is_postgresql:
                    with self.connection.raw.transaction():
                        self._serialize()
                        yield
                else:
                    self.connection.execute("BEGIN IMMEDIATE")
                    try:
                        self._serialize()
                        yield
                        self.connection.commit()
                    except BaseException:
                        self.connection.raw.rollback()
                        raise
            except OwaError:
                raise
            except Exception:
                raise WorkflowExecutionError(
                    "durable event transaction could not be stored"
                ) from None

    @contextmanager
    def _owned(self, identity: EffectIdentity, lease: ExecutionLease | None) -> Iterator[None]:
        with self._lock:
            if lease is None:
                with self.transaction():
                    yield
            else:
                try:
                    with owned_write_transaction(
                        self.connection,
                        lease,
                        invocation_id=identity.invocation_id,
                        workflow_fingerprint=identity.workflow_fingerprint,
                    ):
                        self._serialize()
                        yield
                except OwaError:
                    raise
                except Exception:
                    raise WorkflowExecutionError(
                        "durable event transaction could not be stored"
                    ) from None

    def bind_invocation(
        self,
        invocation_id: str,
        fingerprint: str,
        scope: str,
        execution_lease: ExecutionLease | None = None,
    ) -> None:
        """Bind the corrected event profile before native execution starts."""
        self._scope(scope)
        identity = EffectIdentity(
            invocation_id, fingerprint, "/workflow", (("/workflow", 1),), "event:profile:v1"
        )
        digest = _digest(["owa/events/v1", invocation_id, fingerprint, scope])
        with self._owned(identity, execution_lease):
            self.connection.execute(
                """INSERT INTO event_invocation_profiles(
                invocation_id, fingerprint, scope, profile, digest)
                VALUES (?, ?, ?, 'owa/events/v1', ?) ON CONFLICT(invocation_id) DO NOTHING""",
                (invocation_id, fingerprint, scope, digest),
            )
            self._verify_profile(invocation_id, fingerprint, scope)

    def _verify_profile(
        self, invocation_id: str, fingerprint: str, scope: str | None = None
    ) -> None:
        row = self.connection.execute(
            "SELECT fingerprint, scope, profile, digest FROM event_invocation_profiles "
            "WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        if row is None or (
            row[0] != fingerprint
            or row[2] != "owa/events/v1"
            or (scope is not None and row[1] != scope)
            or row[3] != _digest(["owa/events/v1", invocation_id, fingerprint, row[1]])
        ):
            raise WorkflowExecutionError(
                "durable event invocation profile is unavailable or incompatible"
            )

    def arrivals(
        self, *, scope: str = "anonymous", after: int = 0
    ) -> tuple[tuple[int, EventEnvelope], ...]:
        """Read bounded arrivals for generic consumers without workflow receipts."""
        self._scope(scope)
        if type(after) is not int or after < 0:
            raise ValueError("event arrival cursor is invalid")
        with self._lock:
            rows = self.connection.execute(
                "SELECT sequence, envelope, digest FROM event_inbox "
                "WHERE scope = ? AND sequence > ? "
                "ORDER BY sequence LIMIT ?",
                (scope, after, self.limits.scan_batch),
            ).fetchall()
            return tuple(
                (sequence, self._decode_envelope(encoded, digest))
                for sequence, encoded, digest in rows
            )

    def arrivals_in_transaction(
        self,
        *,
        scope: str,
        after: int,
        limit: int | None = None,
    ) -> tuple[EventArrival, ...]:
        """Read committed candidates inside the caller's existing event transaction."""
        self._scope(scope)
        maximum = self.limits.scan_batch if limit is None else limit
        if (
            type(after) is not int
            or after < 0
            or type(maximum) is not int
            or not 0 < maximum <= self.limits.scan_batch
        ):
            raise EventValidationError("event consumer scan is invalid")
        rows = self.connection.execute(
            "SELECT sequence,envelope,digest FROM event_inbox WHERE scope=? AND sequence>? "
            "ORDER BY sequence LIMIT ?",
            (scope, after, maximum),
        ).fetchall()
        return tuple(
            EventArrival(sequence, self._decode_envelope(encoded, digest), digest)
            for sequence, encoded, digest in rows
        )

    def consumer_pin_in_transaction(self, consumer_key: str) -> EventConsumerPin | None:
        row = self.connection.execute(
            "SELECT scope,cursor,active FROM event_consumer_pins WHERE consumer_key=?",
            (consumer_key,),
        ).fetchone()
        if row is None:
            return None
        if type(row[1]) is not int or row[1] < 0 or row[2] not in (0, 1):
            raise WorkflowExecutionError("event consumer retention evidence is inconsistent")
        self._scope(row[0])
        return EventConsumerPin(consumer_key, row[0], row[1], bool(row[2]))

    def pin_consumer_in_transaction(
        self,
        pin: EventConsumerPin,
        *,
        selected: tuple[EventArrival, ...] = (),
    ) -> None:
        """Advance a typed external consumer's cursor and retain immutable selections."""
        self._scope(pin.scope)
        if (
            not pin.consumer_key
            or len(pin.consumer_key.encode()) > 1024
            or type(pin.cursor) is not int
            or pin.cursor < 0
            or type(pin.active) is not bool
        ):
            raise EventValidationError("event consumer retention pin is invalid")
        existing = self.consumer_pin_in_transaction(pin.consumer_key)
        maximum = self.connection.execute(
            "SELECT value FROM event_counters WHERE counter='arrival'"
        ).fetchone()
        if maximum is None or pin.cursor > maximum[0]:
            raise WorkflowExecutionError("event consumer cursor exceeds admitted arrivals")
        if existing is None:
            count = self.connection.execute(
                "SELECT (SELECT COUNT(*) FROM event_subscriptions) + "
                "(SELECT COUNT(*) FROM event_consumer_pins)"
            ).fetchone()[0]
            if count >= self.limits.max_subscriptions:
                raise EventValidationError("durable event subscription capacity is exhausted")
        elif (
            existing.scope != pin.scope
            or existing.cursor > pin.cursor
            or (not existing.active and pin.active)
        ):
            raise WorkflowExecutionError("event consumer retention cursor changed its binding")
        for arrival in selected:
            row = self.connection.execute(
                "SELECT scope,envelope,digest FROM event_inbox WHERE sequence=?",
                (arrival.sequence,),
            ).fetchone()
            if (
                row is None
                or row[0] != pin.scope
                or row[2] != arrival.digest
                or _json(self._decode_envelope(row[1], row[2]).as_dict())
                != _json(arrival.envelope.as_dict())
                or arrival.sequence > pin.cursor
            ):
                raise WorkflowExecutionError("event consumer selection evidence disagrees")
            previous = self.connection.execute(
                "SELECT digest FROM event_consumer_selections WHERE consumer_key=? "
                "AND inbox_sequence=?",
                (pin.consumer_key, arrival.sequence),
            ).fetchone()
            if previous is not None and previous[0] != arrival.digest:
                raise WorkflowExecutionError("event consumer selection digest changed")
            self.connection.execute(
                "INSERT INTO event_consumer_selections(consumer_key,inbox_sequence,digest) "
                "VALUES (?, ?, ?) ON CONFLICT(consumer_key,inbox_sequence) DO NOTHING",
                (pin.consumer_key, arrival.sequence, arrival.digest),
            )
        self.connection.execute(
            "INSERT INTO event_consumer_pins(consumer_key,scope,cursor,active) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(consumer_key) DO UPDATE SET cursor=excluded.cursor,active=excluded.active",
            (pin.consumer_key, pin.scope, pin.cursor, int(pin.active)),
        )

    def verify_consumer_selection_in_transaction(
        self,
        consumer_key: str,
        *,
        scope: str,
        sequence: int,
        digest: str,
    ) -> EventArrival:
        selected = self.connection.execute(
            "SELECT digest FROM event_consumer_selections WHERE consumer_key=? "
            "AND inbox_sequence=?",
            (consumer_key, sequence),
        ).fetchone()
        row = self.connection.execute(
            "SELECT scope,envelope,digest FROM event_inbox WHERE sequence=?",
            (sequence,),
        ).fetchone()
        if (
            selected is None
            or selected[0] != digest
            or row is None
            or row[0] != scope
            or row[2] != digest
        ):
            raise WorkflowExecutionError("event consumer selection evidence is missing or changed")
        return EventArrival(sequence, self._decode_envelope(row[1], row[2]), digest)

    def _scope(self, scope: str) -> None:
        if not isinstance(scope, str) or not scope or len(scope.encode()) > 1024:
            raise EventValidationError("event delivery scope is invalid")

    def _bound_scope(self, identity: EffectIdentity, scope: str) -> None:
        if (
            self.connection.execute(
                "SELECT 1 FROM event_invocation_profiles WHERE invocation_id = ?",
                (identity.invocation_id,),
            ).fetchone()
            is not None
        ):
            self._verify_profile(identity.invocation_id, identity.workflow_fingerprint, scope)

    def _envelope(self, properties: Mapping[str, Any], default_source: str) -> EventEnvelope:
        try:
            validate_expression_data(properties)
            envelope = _make_envelope(properties, default_source=default_source)
            encoded = _json(envelope.as_dict())
            if len(encoded.encode()) > self.limits.max_event_bytes:
                raise ValueError("event exceeds the configured size limit")
            if not envelope.id or not envelope.source or len(envelope.id) > 1024:
                raise ValueError("event identity is invalid")
            return envelope
        except Exception:
            raise EventValidationError(
                "event envelope is invalid or exceeds configured bounds"
            ) from None

    def _decode_envelope(self, encoded: str, digest: str) -> EventEnvelope:
        try:
            value = json.loads(encoded)
            if not isinstance(value, dict) or _digest(value) != digest:
                raise ValueError("event digest mismatch")
            envelope = _make_envelope(value, default_source=value["source"])
            if _json(envelope.as_dict()) != _json(value):
                raise ValueError("event canonical shape mismatch")
            return envelope
        except Exception:
            raise WorkflowExecutionError(
                "durable event evidence is unavailable or inconsistent"
            ) from None

    def _capacity(self, envelope: EventEnvelope, scope: str, *, outbox: bool = False) -> None:
        """Reserve future inbox space before acknowledging a queued publication."""
        count, inbox_bytes = self.connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM event_inbox"
        ).fetchone()
        rows = self.connection.execute(
            "SELECT scope, envelope, digest, delivered, bytes FROM event_outbox"
        ).fetchall()
        key = (scope, envelope.source, envelope.id)
        reservations: dict[tuple[str, str, str], int] = {}
        outbox_bytes = 0
        for saved_scope, encoded, digest, delivered, size in rows:
            outbox_bytes += size
            if delivered:
                continue
            saved = self._decode_envelope(encoded, digest)
            saved_key = (saved_scope, saved.source, saved.id)
            existing = self.connection.execute(
                "SELECT 1 FROM event_inbox WHERE scope = ? AND source = ? AND event_id = ?",
                saved_key,
            ).fetchone()
            if existing is None:
                reservations[saved_key] = size
        existing = self.connection.execute(
            "SELECT 1 FROM event_inbox WHERE scope = ? AND source = ? AND event_id = ?", key
        ).fetchone()
        size = len(_json(envelope.as_dict()).encode())
        total_count = count + len(reservations)
        total_bytes = inbox_bytes + outbox_bytes + sum(reservations.values())
        if outbox:
            if len(rows) >= self.limits.max_outbox:
                raise EventValidationError("durable event storage capacity is exhausted")
            total_bytes += size
            if existing is None and key not in reservations:
                total_count += 1
                total_bytes += size
        else:
            # Delivery exchanges a reservation for an inbox row, rather than
            # competing with publications whose acknowledgement is committed.
            total_count += 0 if key in reservations else 1
            total_bytes += size - reservations.get(key, 0)
        if total_count > self.limits.max_retained_events or (
            total_bytes > self.limits.max_retained_bytes
        ):
            raise EventValidationError("durable event storage capacity is exhausted")

    def _known_envelope(self, scope: str, envelope: EventEnvelope) -> EventEnvelope | None:
        row = self.connection.execute(
            "SELECT envelope, digest FROM event_inbox "
            "WHERE scope = ? AND source = ? AND event_id = ?",
            (scope, envelope.source, envelope.id),
        ).fetchone()
        if row is not None:
            return self._decode_envelope(row[0], row[1])
        rows = self.connection.execute(
            "SELECT envelope, digest FROM event_outbox WHERE scope = ?", (scope,)
        ).fetchall()
        for encoded, digest in rows:
            saved = self._decode_envelope(encoded, digest)
            if saved.source == envelope.source and saved.id == envelope.id:
                return saved
        return None

    def _canonical_submission(
        self, properties: Mapping[str, Any], default_source: str, scope: str
    ) -> EventEnvelope:
        proposed = self._envelope(properties, default_source)
        saved = self._known_envelope(scope, proposed)
        if saved is None:
            return proposed
        incoming = proposed.as_dict()
        recorded = saved.as_dict()
        if "time" not in properties:
            incoming.pop("time", None)
            recorded.pop("time", None)
        if _json(incoming) != _json(recorded):
            raise EventValidationError("event identity is already bound to different content")
        return saved

    def ingest(
        self,
        properties: Mapping[str, Any],
        *,
        default_source: str,
        scope: str = "anonymous",
        participant: EventParticipant | None = None,
    ) -> EventEnvelope:
        with self.transaction():
            return self.ingest_in_transaction(
                properties, default_source=default_source, scope=scope, participant=participant
            )

    def ingest_in_transaction(
        self,
        properties: Mapping[str, Any],
        *,
        default_source: str,
        scope: str = "anonymous",
        participant: EventParticipant | None = None,
    ) -> EventEnvelope:
        """Accept inside the caller-owned transaction; never commit here."""
        self._scope(scope)
        proposed = self._canonical_submission(properties, default_source, scope)
        submitted = {**dict(properties), "id": proposed.id, "source": proposed.source}
        submitted_digest = _digest(submitted)
        row = self.connection.execute(
            "SELECT envelope, digest, submitted_digest FROM event_inbox "
            "WHERE scope = ? AND source = ? AND event_id = ?",
            (scope, proposed.source, proposed.id),
        ).fetchone()
        if row is not None:
            envelope = self._decode_envelope(row[0], row[1])
            if row[2] != submitted_digest and _json(envelope.as_dict()) != _json(
                proposed.as_dict()
            ):
                raise EventValidationError("event identity is already bound to different content")
            if participant is not None:
                participant(self.connection, envelope)
            return envelope
        encoded = _json(proposed.as_dict())
        self._capacity(proposed, scope)
        # A shared counter writer serializes arrival order and concurrent
        # source/id admissions on both supported DB implementations.
        sequence = self.connection.execute(
            "UPDATE event_counters SET value = value + 1 WHERE counter = 'arrival' RETURNING value"
        ).fetchone()[0]
        self.connection.execute(
            """INSERT INTO event_inbox(sequence, scope, source, event_id, envelope, digest,
            submitted_digest, created_at, bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sequence,
                scope,
                proposed.source,
                proposed.id,
                encoded,
                _digest(proposed.as_dict()),
                submitted_digest,
                time.time(),
                len(encoded.encode()),
            ),
        )
        if participant is not None:
            participant(self.connection, proposed)
        return proposed

    def publish_effect(
        self,
        identity: EffectIdentity,
        request_digest: str,
        properties: Mapping[str, Any],
        *,
        default_source: str,
        scope: str = "anonymous",
        execution_lease: ExecutionLease | None = None,
        operation_id: str | None = None,
        participant: EventParticipant | None = None,
    ) -> EffectReceipt:
        if identity.operation != "emit":
            raise WorkflowExecutionError("event publication requires an emit identity")
        self._scope(scope)
        with self._owned(identity, execution_lease):
            self._bound_scope(identity, scope)
            existing = self.journal.get(identity)
            if existing is not None:
                if existing.status != "completed" or existing.request_digest != request_digest:
                    raise WorkflowExecutionError("durable publication request is inconsistent")
                self._verify_outbox(identity, existing)
                return existing
            if self._invocation_terminal(identity.invocation_id):
                raise InvocationStateConflict("terminal invocation cannot publish a new event")
            envelope = self._canonical_submission(properties, default_source, scope)
            encoded = _json(envelope.as_dict())
            self._capacity(envelope, scope, outbox=True)
            if participant is not None:
                participant(self.connection, envelope)
            self.connection.execute(
                """INSERT INTO event_outbox(identity, invocation_id, fingerprint, scope, envelope,
                digest, request_digest, delivered, created_at, bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    identity.key,
                    identity.invocation_id,
                    identity.workflow_fingerprint,
                    scope,
                    encoded,
                    _digest(envelope.as_dict()),
                    request_digest,
                    time.time(),
                    len(encoded.encode()),
                ),
            )
            receipt = EffectReceipt(
                request_digest,
                "event",
                status="completed",
                output=envelope.as_dict(),
                operation_id=operation_id,
            )
            insert_completed_receipt(self.connection, identity, receipt)
            return receipt

    def deliver_outbox(self, *, limit: int = 128) -> int:
        if type(limit) is not int or limit < 1 or limit > self.limits.scan_batch:
            raise ValueError("outbox delivery batch is invalid")
        with self.transaction():
            query = "SELECT identity, scope, envelope, digest FROM event_outbox "
            query += "WHERE delivered = 0 ORDER BY created_at, identity LIMIT ?"
            if self.connection.is_postgresql:
                query += " FOR UPDATE"
            rows = self.connection.execute(query, (limit,)).fetchall()
            for identity, scope, encoded, digest in rows:
                envelope = self._decode_envelope(encoded, digest)
                self.ingest_in_transaction(
                    envelope.as_dict(), default_source=envelope.source, scope=scope
                )
                self.connection.execute(
                    "UPDATE event_outbox SET delivered = 1 WHERE identity = ? AND delivered = 0",
                    (identity,),
                )
            return len(rows)

    def register(
        self,
        identity: EffectIdentity,
        strategy: Mapping[str, Any],
        *,
        scope: str = "anonymous",
        execution_lease: ExecutionLease | None = None,
    ) -> EventRegistration:
        if identity.operation != "listen":
            raise WorkflowExecutionError("event subscription requires a listen identity")
        self._scope(scope)
        validate_expression_data(strategy)
        if len(_json(strategy).encode()) > self.limits.max_event_bytes:
            raise EventValidationError("event subscription exceeds configured bounds")
        digest = effect_request_digest("listen", {"strategy": strategy, "scope": scope})
        registration = EventRegistration(identity, digest, scope)
        with self._owned(identity, execution_lease):
            self._bound_scope(identity, scope)
            row = self.connection.execute(
                "SELECT payload FROM event_subscriptions WHERE identity = ?", (identity.key,)
            ).fetchone()
            if row is not None:
                self.state(registration)
                return registration
            if self._invocation_terminal(identity.invocation_id):
                raise InvocationStateConflict(
                    "terminal invocation cannot create an event subscription"
                )
            count = self.connection.execute(
                "SELECT (SELECT COUNT(*) FROM event_subscriptions) + "
                "(SELECT COUNT(*) FROM event_consumer_pins)"
            ).fetchone()[0]
            if count >= self.limits.max_subscriptions:
                raise EventValidationError("durable event subscription capacity is exhausted")
            self.connection.execute(
                "INSERT INTO event_consumers(invocation_id, cursor) VALUES (?, 0) "
                "ON CONFLICT(invocation_id) DO NOTHING",
                (identity.invocation_id,),
            )
            cursor_query = "SELECT cursor FROM event_consumers WHERE invocation_id = ?"
            if self.connection.is_postgresql:
                cursor_query += " FOR UPDATE"
            cursor = self.connection.execute(cursor_query, (identity.invocation_id,)).fetchone()[0]
            payload = {
                "profile": "owa/events/v1",
                "identity": _identity_value(identity),
                "request_digest": digest,
                "scope": scope,
                "strategy": dict(strategy),
                "cursor": cursor,
                "initial_cursor": cursor,
                "correlations": {},
                "matched_filters": [],
                "progress": {},
                "selection_count": 0,
                "decision_count": 0,
            }
            self.connection.execute(
                """INSERT INTO event_subscriptions(identity, invocation_id, fingerprint,
                payload, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)""",
                (
                    identity.key,
                    identity.invocation_id,
                    identity.workflow_fingerprint,
                    _json(payload),
                    time.time(),
                ),
            )
            registration_identity = replace(identity, operation="listen:registration:v1")
            insert_completed_receipt(
                self.connection,
                registration_identity,
                EffectReceipt(
                    digest,
                    "event",
                    status="completed",
                    output={
                        "profile": "owa/events/v1",
                        "identity": _identity_value(identity),
                        "scope": scope,
                        "strategy": dict(strategy),
                        "initial_cursor": cursor,
                    },
                ),
            )
        return registration

    def _row(
        self, registration: EventRegistration, *, lock: bool = False
    ) -> tuple[dict[str, Any], str]:
        query = "SELECT invocation_id, fingerprint, payload, status FROM event_subscriptions "
        query += "WHERE identity = ?"
        if lock and self.connection.is_postgresql:
            query += " FOR UPDATE"
        row = self.connection.execute(query, (registration.identity.key,)).fetchone()
        if row is None:
            raise WorkflowExecutionError("durable event subscription evidence is unavailable")
        try:
            payload = json.loads(row[2])
            identity = registration.identity
            if (
                set(payload)
                != {
                    "profile",
                    "identity",
                    "request_digest",
                    "scope",
                    "strategy",
                    "cursor",
                    "initial_cursor",
                    "correlations",
                    "matched_filters",
                    "progress",
                    "selection_count",
                    "decision_count",
                }
                or payload["profile"] != "owa/events/v1"
                or EffectIdentity.from_value(payload["identity"]) != identity
                or row[0] != identity.invocation_id
                or row[1] != identity.workflow_fingerprint
                or payload["scope"] != registration.scope
                or payload["request_digest"] != registration.request_digest
                or payload["request_digest"]
                != effect_request_digest(
                    "listen", {"strategy": payload["strategy"], "scope": payload["scope"]}
                )
                or row[3] not in {"pending", "completed", "closed"}
                or type(payload["cursor"]) is not int
                or payload["cursor"] < 0
                or type(payload["initial_cursor"]) is not int
                or payload["initial_cursor"] > payload["cursor"]
                or type(payload["selection_count"]) is not int
                or payload["selection_count"] < 0
                or type(payload["decision_count"]) is not int
                or payload["decision_count"] < 0
                or not isinstance(payload["correlations"], dict)
                or not isinstance(payload["progress"], dict)
                or not isinstance(payload["matched_filters"], list)
                or any(type(i) is not int or i < 0 for i in payload["matched_filters"])
            ):
                raise ValueError("invalid subscription")
            validate_expression_data(payload)
            registration_receipt = self.journal.get(
                replace(identity, operation="listen:registration:v1")
            )
            if (
                registration_receipt is None
                or registration_receipt.status != "completed"
                or (
                    registration_receipt.request_digest != registration.request_digest
                    or registration_receipt.output
                    != {
                        "profile": "owa/events/v1",
                        "identity": _identity_value(identity),
                        "scope": registration.scope,
                        "strategy": payload["strategy"],
                        "initial_cursor": payload["initial_cursor"],
                    }
                )
            ):
                raise ValueError("event registration receipt mismatch")
            if payload["decision_count"]:
                decision_receipt = self.journal.get(
                    registration.decision_identity(payload["decision_count"])
                )
                if (
                    decision_receipt is None
                    or decision_receipt.status != "completed"
                    or decision_receipt.request_digest
                    != self._decision_digest(registration, payload["decision_count"])
                    or _json(decision_receipt.output) != _json(self._routing_value(payload, row[3]))
                ):
                    raise ValueError("event routing progress receipt mismatch")
            elif (
                row[3] != "pending"
                or payload["cursor"] != payload["initial_cursor"]
                or payload["selection_count"]
                or payload["correlations"]
                or payload["matched_filters"]
                or payload["progress"]
            ):
                raise ValueError("event routing changed without a decision receipt")
            return payload, row[3]
        except Exception:
            raise WorkflowExecutionError(
                "durable event subscription evidence is inconsistent"
            ) from None

    def state(self, registration: EventRegistration) -> EventProgress:
        with self._lock:
            payload, status = self._row(registration)
            rows = self.connection.execute(
                """SELECT ordinal, inbox_sequence, digest, receipt_identity FROM event_selections
                WHERE subscription = ? ORDER BY ordinal""",
                (registration.identity.key,),
            ).fetchall()
            if len(rows) != payload["selection_count"]:
                raise WorkflowExecutionError("durable selected event evidence is unavailable")
            events: list[EventEnvelope] = []
            previous = payload["initial_cursor"]
            for expected_ordinal, (ordinal, sequence, digest, receipt_key) in enumerate(rows, 1):
                event_row = self.connection.execute(
                    "SELECT envelope, digest, scope FROM event_inbox WHERE sequence = ?",
                    (sequence,),
                ).fetchone()
                if (
                    ordinal != expected_ordinal
                    or sequence <= previous
                    or sequence > payload["cursor"]
                    or event_row is None
                    or event_row[1] != digest
                    or event_row[2] != registration.scope
                ):
                    raise WorkflowExecutionError("durable selected event evidence is inconsistent")
                envelope = self._decode_envelope(event_row[0], digest)
                identity = event_selection_identity(registration, ordinal)
                receipt = self.journal.get(identity)
                if (
                    receipt_key != identity.key
                    or receipt is None
                    or receipt.status != "completed"
                    or receipt.request_digest
                    != self._selection_digest(registration, ordinal, sequence, digest)
                    or _json(receipt.output) != _json(envelope.as_dict())
                ):
                    raise WorkflowExecutionError("durable event selection receipt is inconsistent")
                events.append(envelope)
                previous = sequence
            final = self.journal.get(registration.identity)
            if status == "completed":
                if (
                    final is None
                    or final.status != "completed"
                    or final.request_digest != registration.request_digest
                    or _json(final.output) != _json([event.as_dict() for event in events])
                ):
                    raise WorkflowExecutionError("durable listener completion is inconsistent")
            elif final is not None:
                raise WorkflowExecutionError("unfinished durable listener has a terminal receipt")
            return EventProgress(
                registration,
                tuple(events),
                payload["correlations"],
                tuple(payload["matched_filters"]),
                payload["progress"],
                payload["cursor"],
                completed=status == "completed",
                closed=status == "closed",
                receipt=final,
                decision_ordinal=payload["decision_count"],
                decision_receipt=self.journal.get(
                    registration.decision_identity(payload["decision_count"])
                )
                if payload["decision_count"]
                else None,
            )

    @staticmethod
    def _routing_value(payload: Mapping[str, Any], status: str) -> dict[str, Any]:
        return {
            "profile": "owa/events/progress/v1",
            "status": status,
            **{
                key: payload[key]
                for key in (
                    "cursor",
                    "correlations",
                    "matched_filters",
                    "progress",
                    "selection_count",
                    "decision_count",
                )
            },
        }

    @staticmethod
    def _decision_digest(registration: EventRegistration, ordinal: int) -> str:
        return effect_request_digest(
            "listen:progress:v1", {"subscription": registration.request_digest, "ordinal": ordinal}
        )

    def _save_progress(
        self, registration: EventRegistration, payload: dict[str, Any], status: str
    ) -> None:
        payload["decision_count"] += 1
        identity = registration.decision_identity(payload["decision_count"])
        receipt = EffectReceipt(
            self._decision_digest(registration, payload["decision_count"]),
            "event",
            status="completed",
            output=self._routing_value(payload, status),
        )
        insert_completed_receipt(self.connection, identity, receipt)
        self.connection.execute(
            "UPDATE event_subscriptions SET payload = ?, status = ? WHERE identity = ?",
            (_json(payload), status, registration.identity.key),
        )

    @staticmethod
    def _selection_digest(reg: EventRegistration, ordinal: int, sequence: int, digest: str) -> str:
        return effect_request_digest(
            "listen:event:v1",
            {
                "subscription": reg.request_digest,
                "ordinal": ordinal,
                "sequence": sequence,
                "event_digest": digest,
            },
        )

    def select(
        self,
        registration: EventRegistration,
        *,
        evaluate_candidate: EventEvaluator,
        execution_lease: ExecutionLease | None = None,
    ) -> EventProgress:
        with self._owned(registration.identity, execution_lease):
            payload, status = self._row(registration, lock=True)
            current = self.state(registration)
            if status != "pending":
                return current
            if self._invocation_terminal(registration.identity.invocation_id):
                self._save_progress(registration, payload, "closed")
                return self.state(registration)
            rows = self.connection.execute(
                "SELECT sequence, envelope, digest FROM event_inbox "
                "WHERE scope = ? AND sequence > ? ORDER BY sequence LIMIT ?",
                (registration.scope, payload["cursor"], self.limits.scan_batch),
            ).fetchall()
            for sequence, encoded, digest in rows:
                envelope = self._decode_envelope(encoded, digest)
                decision = evaluate_candidate(envelope, current)
                payload["cursor"] = sequence
                if decision is None:
                    continue
                if not isinstance(decision, EventDecision):
                    raise WorkflowExecutionError("event matcher returned an invalid decision")
                if (
                    any(type(i) is not int or i < 0 for i in decision.filter_indices)
                    or type(decision.terminal) is not bool
                    or type(decision.terminator) is not bool
                ):
                    raise WorkflowExecutionError("event matcher decision fields are invalid")
                correlations = dict(current.correlations)
                for key, value in decision.correlations.items():
                    if key in correlations and _json(correlations[key]) != _json(value):
                        raise WorkflowExecutionError(
                            "durable event correlation changed its binding"
                        )
                    correlations[key] = value
                payload["correlations"] = correlations
                payload["matched_filters"] = sorted(
                    set(current.matched_filters) | set(decision.filter_indices)
                )
                payload["progress"] = dict(decision.progress)
                if (
                    len(
                        _json(
                            {
                                "correlations": payload["correlations"],
                                "progress": payload["progress"],
                            }
                        ).encode()
                    )
                    > self.limits.max_event_bytes
                ):
                    raise EventValidationError("event selection state exceeds configured bounds")
                selected_receipt: EffectReceipt | None = None
                if not decision.terminator:
                    ordinal = payload["selection_count"] + 1
                    selection_id = event_selection_identity(registration, ordinal)
                    selected_receipt = EffectReceipt(
                        self._selection_digest(registration, ordinal, sequence, digest),
                        "event",
                        status="completed",
                        output=envelope.as_dict(),
                    )
                    insert_completed_receipt(self.connection, selection_id, selected_receipt)
                    self.connection.execute(
                        """INSERT INTO event_selections(subscription, ordinal, inbox_sequence,
                        digest, receipt_identity) VALUES (?, ?, ?, ?, ?)""",
                        (registration.identity.key, ordinal, sequence, digest, selection_id.key),
                    )
                    payload["selection_count"] = ordinal
                # Existing registered cohorts preserve their own lower bound;
                # only subsequently created subscriptions consult this watermark.
                self.connection.execute(
                    "UPDATE event_consumers SET cursor = "
                    "CASE WHEN cursor < ? THEN ? ELSE cursor END "
                    "WHERE invocation_id = ?",
                    (sequence, sequence, registration.identity.invocation_id),
                )
                status = "completed" if decision.terminal else "pending"
                self._save_progress(registration, payload, status)
                if decision.terminal:
                    outputs = [event.as_dict() for event in current.events]
                    if not decision.terminator:
                        outputs.append(envelope.as_dict())
                    final = EffectReceipt(
                        registration.request_digest, "event", status="completed", output=outputs
                    )
                    insert_completed_receipt(self.connection, registration.identity, final)
                result = self.state(registration)
                return replace(
                    result,
                    selected=None if decision.terminator else envelope,
                    event_receipt=selected_receipt,
                )
            if rows:
                self._save_progress(registration, payload, status)
                return self.state(registration)
            return current

    def complete(
        self, registration: EventRegistration, *, execution_lease: ExecutionLease | None = None
    ) -> EventProgress:
        """Complete a strategy without a new arrival, including an empty all."""
        with self._owned(registration.identity, execution_lease):
            payload, _ = self._row(registration, lock=True)
            current = self.state(registration)
            if current.closed or current.completed:
                return current
            if self._invocation_terminal(registration.identity.invocation_id):
                self._save_progress(registration, payload, "closed")
                return self.state(registration)
            receipt = EffectReceipt(
                registration.request_digest,
                "event",
                status="completed",
                output=[event.as_dict() for event in current.events],
            )
            insert_completed_receipt(self.connection, registration.identity, receipt)
            self._save_progress(registration, payload, "completed")
            return self.state(registration)

    def abandon(
        self, registration: EventRegistration, *, execution_lease: ExecutionLease | None = None
    ) -> None:
        with self._owned(registration.identity, execution_lease):
            payload, status = self._row(registration, lock=True)
            if status == "pending":
                self._save_progress(registration, payload, "closed")

    def _invocation_terminal(self, invocation_id: str) -> bool:
        # A store can be used independently in unit tests; journal's optional
        # invocation service is authoritative when composed in the runtime.
        store = self.journal._invocations
        handle = store.get(invocation_id) if store is not None else None
        return handle is not None and handle.status in {"completed", "faulted", "cancelled"}

    def close_invocation(self, invocation_id: str, fingerprint: str) -> None:
        """Dispose pending subscriptions only after common terminal state commits."""
        with self.transaction():
            store = self.journal._invocations
            handle = store.get(invocation_id) if store is not None else None
            if (
                handle is None
                or handle.workflow_fingerprint != fingerprint
                or (handle.status not in {"completed", "faulted", "cancelled"})
            ):
                raise InvocationStateConflict("event disposition requires a terminal invocation")
            rows = self.connection.execute(
                "SELECT payload FROM event_subscriptions WHERE invocation_id = ? "
                "AND fingerprint = ? AND status = 'pending'",
                (invocation_id, fingerprint),
            ).fetchall()
            for (encoded,) in rows:
                value = json.loads(encoded)
                registration = EventRegistration(
                    EffectIdentity.from_value(value["identity"]),
                    value["request_digest"],
                    value["scope"],
                )
                payload, _ = self._row(registration, lock=True)
                self._save_progress(registration, payload, "closed")

    def _verify_outbox(self, identity: EffectIdentity, receipt: EffectReceipt) -> None:
        row = self.connection.execute(
            "SELECT invocation_id, fingerprint, envelope, digest, request_digest, delivered, scope "
            "FROM event_outbox WHERE identity = ?",
            (identity.key,),
        ).fetchone()
        if row is None or (
            row[0] != identity.invocation_id
            or row[1] != identity.workflow_fingerprint
            or row[4] != receipt.request_digest
            or type(row[5]) is not int
            or row[5] not in {0, 1}
        ):
            raise WorkflowExecutionError(
                "durable publication evidence is unavailable or inconsistent"
            )
        envelope = self._decode_envelope(row[2], row[3])
        if _json(receipt.output) != _json(envelope.as_dict()):
            raise WorkflowExecutionError("durable publication receipt differs from its envelope")
        if row[5] == 1:
            inbox = self.connection.execute(
                "SELECT digest FROM event_inbox WHERE scope = ? AND source = ? AND event_id = ?",
                (row[6], envelope.source, envelope.id),
            ).fetchone()
            if inbox is None or inbox[0] != row[3]:
                raise WorkflowExecutionError("delivered publication evidence is unavailable")

    def verify_invocation(
        self, invocation_id: str, fingerprint: str, *, require_profile: bool = False
    ) -> None:
        self._verify_invocation(invocation_id, fingerprint, require_profile, set())

    def _verify_invocation(
        self,
        invocation_id: str,
        fingerprint: str,
        require_profile: bool,
        visited: set[str],
        ancestors: frozenset[str] | None = None,
    ) -> None:
        try:
            lineage = ancestors or frozenset()
            if invocation_id in lineage:
                raise ValueError("durable event child dependency cycle")
            if invocation_id in visited:
                return
            visited.add(invocation_id)
            with self._lock:
                if require_profile:
                    self._verify_profile(invocation_id, fingerprint)
                rows = self.connection.execute(
                    "SELECT identity, payload, fingerprint FROM event_subscriptions "
                    "WHERE invocation_id = ?",
                    (invocation_id,),
                ).fetchall()
                for key, encoded, saved_fingerprint in rows:
                    payload = json.loads(encoded)
                    identity = EffectIdentity.from_value(payload["identity"])
                    if (
                        identity.key != key
                        or saved_fingerprint != fingerprint
                        or (identity.workflow_fingerprint != fingerprint)
                    ):
                        raise ValueError("event subscription identity mismatch")
                    self.state(
                        EventRegistration(identity, payload["request_digest"], payload["scope"])
                    )
                outbox = self.connection.execute(
                    "SELECT identity, fingerprint FROM event_outbox WHERE invocation_id = ?",
                    (invocation_id,),
                ).fetchall()
                for key, saved_fingerprint in outbox:
                    index = self.connection.execute(
                        "SELECT payload FROM workflow_effect_invocations WHERE identity = ?", (key,)
                    ).fetchone()
                    if saved_fingerprint != fingerprint or index is None:
                        raise ValueError("event publication identity mismatch")
                    identity = EffectIdentity.from_value(json.loads(index[0]))
                    receipt = self.journal.get(identity)
                    if (
                        identity.invocation_id != invocation_id
                        or identity.key != key
                        or receipt is None
                    ):
                        raise ValueError("event publication receipt missing")
                    self._verify_outbox(identity, receipt)
                # Also find deleted subscription/outbox rows through the atomic
                # journal identity index; absence must never become permission
                # for a fresh dispatch on native resume.
                identities = self.connection.execute(
                    "SELECT payload FROM workflow_effect_invocations WHERE invocation_id = ?",
                    (invocation_id,),
                ).fetchall()
                for (encoded,) in identities:
                    identity = EffectIdentity.from_value(json.loads(encoded))
                    receipt = self.journal.get(identity)
                    if receipt is None:
                        raise ValueError("admitted action receipt is missing")
                    if receipt.child_invocation is not None:
                        invocations = self.journal._invocations
                        child = (
                            invocations.get(receipt.child_invocation.invocation_id)
                            if invocations
                            else None
                        )
                        if child is None:
                            raise ValueError("durable event child invocation is unavailable")
                        child_indexes = self.connection.execute(
                            "SELECT payload FROM workflow_effect_invocations "
                            "WHERE invocation_id = ?",
                            (child.invocation_id,),
                        ).fetchall()
                        needs_profile = self.connection.execute(
                            "SELECT 1 FROM event_invocation_profiles WHERE invocation_id = ?",
                            (child.invocation_id,),
                        ).fetchone() is not None or any(
                            saved is not None and saved.owner == "event"
                            for (value,) in child_indexes
                            for saved in (
                                self.journal.get(EffectIdentity.from_value(json.loads(value))),
                            )
                        )
                        self._verify_invocation(
                            child.invocation_id,
                            child.workflow_fingerprint,
                            needs_profile,
                            visited,
                            lineage | {invocation_id},
                        )
                    if receipt.owner != "event":
                        continue
                    if identity.operation == "emit":
                        self._verify_outbox(identity, receipt)
                    elif identity.operation in {
                        "listen",
                        "listen:registration:v1",
                        "listen:progress:v1",
                    }:
                        if identity.operation == "listen:progress:v1":
                            identity = replace(identity, occurrence=identity.occurrence[:-1])
                        subscription_identity = replace(identity, operation="listen")
                        if (
                            self.connection.execute(
                                "SELECT 1 FROM event_subscriptions WHERE identity = ?",
                                (subscription_identity.key,),
                            ).fetchone()
                            is None
                        ):
                            raise ValueError("event subscription deleted")
                    elif identity.operation == "listen:event:v1":
                        if (
                            self.connection.execute(
                                "SELECT 1 FROM event_selections WHERE receipt_identity = ?",
                                (identity.key,),
                            ).fetchone()
                            is None
                        ):
                            raise ValueError("event selection deleted")
        except Exception:
            raise WorkflowExecutionError(
                "durable event invocation evidence could not be reconciled"
            ) from None

    def prune(self, *, now: float | None = None, limit: int = 128) -> int:
        """Prune only unreferenced expired arrivals; acknowledged evidence stays pinned."""
        cutoff = (time.time() if now is None else now) - self.limits.retention_seconds
        if (
            not math.isfinite(cutoff)
            or type(limit) is not int
            or not 0 < limit <= self.limits.scan_batch
        ):
            raise ValueError("event retention sweep is invalid")
        with self.transaction():
            rows = self.connection.execute(
                "SELECT sequence, scope, source, event_id FROM event_inbox WHERE created_at < ? "
                "AND NOT EXISTS (SELECT 1 FROM event_selections s "
                "WHERE s.inbox_sequence = event_inbox.sequence) "
                "AND NOT EXISTS (SELECT 1 FROM event_consumer_selections s "
                "WHERE s.inbox_sequence = event_inbox.sequence) "
                "ORDER BY sequence LIMIT ?",
                (cutoff, limit),
            ).fetchall()
            removed = 0
            for sequence, scope, source, event_id in rows:
                # A matching scope's paused listener pins its unscanned arrivals.
                # Other scopes and already-scanned rows do not prevent cleanup.
                subscriptions = self.connection.execute(
                    "SELECT payload FROM event_subscriptions WHERE status = 'pending'"
                ).fetchall()
                if any(
                    value["scope"] == scope and value["cursor"] < sequence
                    for (encoded,) in subscriptions
                    for value in (json.loads(encoded),)
                ):
                    continue
                pinned = self.connection.execute(
                    "SELECT 1 FROM event_consumer_pins WHERE scope=? AND active=1 AND cursor<?",
                    (scope, sequence),
                ).fetchone()
                if pinned is not None:
                    continue
                published = self.connection.execute(
                    "SELECT envelope, digest FROM event_outbox WHERE scope = ? AND delivered = 1",
                    (scope,),
                ).fetchall()
                if any(
                    event.source == source and event.id == event_id
                    for encoded, digest in published
                    for event in (self._decode_envelope(encoded, digest),)
                ):
                    continue
                self.connection.execute("DELETE FROM event_inbox WHERE sequence = ?", (sequence,))
                removed += 1
            return removed

    def close(self) -> None:
        """The borrowed journal connection remains owned by RuntimeServices."""
