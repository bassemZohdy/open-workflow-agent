"""Bounded durable human-approval state composed with portable workflow events."""

from __future__ import annotations

import hmac
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .errors import (
    ApprovalAuthorizationError,
    ApprovalConflict,
    ApprovalNotFound,
    ApprovalValidationError,
)
from .events import EventBus, EventEnvelope
from .security import BearerSecurityProfile, SecurityConfig, resolve_secret
from .storage import StorageConnection, open_storage

APPROVAL_REQUEST_EVENT = "io.openworkflow.agent.approval.requested"
APPROVAL_DECISION_EVENT = "io.openworkflow.agent.approval.decided"
APPROVAL_STATES = frozenset({"pending", "approved", "rejected", "expired"})
APPROVAL_DECISIONS = frozenset({"approved", "rejected"})


@dataclass(slots=True)
class ApprovalRecord:
    approval_id: str
    subject: str
    status: str
    request_event_json: str
    requested_at: float
    expires_at: float | None = None
    decision_event_json: str | None = None
    operator_id: str | None = None
    decision_operation_key: str | None = None
    decided_at: float | None = None

    @property
    def request_event(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.request_event_json))

    @property
    def decision_event(self) -> dict[str, Any] | None:
        return (
            cast(dict[str, Any], json.loads(self.decision_event_json))
            if self.decision_event_json
            else None
        )

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "approval_id": self.approval_id,
            "subject": self.subject,
            "status": self.status,
            "request": self.request_event,
            "requested_at": _timestamp(self.requested_at),
        }
        if self.expires_at is not None:
            value["expires_at"] = _timestamp(self.expires_at)
        if self.decision_event is not None:
            value["decision"] = self.decision_event
        if self.operator_id is not None:
            value["operator_id"] = self.operator_id
        if self.decided_at is not None:
            value["decided_at"] = _timestamp(self.decided_at)
        return value


class ApprovalStore:
    """Durable common approval metadata; contains no engine-native state."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.connection: StorageConnection = open_storage(database, "owa_approvals")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            status TEXT NOT NULL,
            request_event TEXT NOT NULL,
            requested_at REAL NOT NULL,
            expires_at REAL,
            decision_event TEXT,
            operator_id TEXT,
            decision_operation_key TEXT UNIQUE,
            decided_at REAL)"""
        )
        self.connection.commit()

    def create_request(self, event: EventEnvelope) -> ApprovalRecord:
        if event.type != APPROVAL_REQUEST_EVENT:
            raise ApprovalValidationError(
                "approval request must use the reserved request event type"
            )
        if not event.subject or event.subject != event.id:
            raise ApprovalValidationError(
                "durable approval requests require subject to equal the stable approval id",
                details={"approval_id": event.id, "subject": event.subject},
            )
        existing = self.get(event.id)
        request_json = event.raw()
        if existing is not None:
            if existing.request_event_json != request_json:
                raise ApprovalConflict(
                    "approval id is already bound to a different request",
                    details={"approval_id": event.id},
                )
            return existing
        expires_at = _parse_expiry(event.extensions.get("approvalexpiresat"))
        requested_at = _parse_event_time(event.time)
        record = ApprovalRecord(
            approval_id=event.id,
            subject=event.subject,
            status="pending",
            request_event_json=request_json,
            requested_at=requested_at,
            expires_at=expires_at,
        )
        self.connection.execute(
            "INSERT INTO approvals(approval_id, subject, status, request_event, requested_at, "
            "expires_at, decision_event, operator_id, decision_operation_key, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.approval_id,
                record.subject,
                record.status,
                record.request_event_json,
                record.requested_at,
                record.expires_at,
                None,
                None,
                None,
                None,
            ),
        )
        self.connection.commit()
        return record

    def get(self, approval_id: str) -> ApprovalRecord | None:
        self._expire_due()
        row = self.connection.execute(
            "SELECT approval_id, subject, status, request_event, requested_at, expires_at, "
            "decision_event, operator_id, decision_operation_key, decided_at "
            "FROM approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        return self._decode(row) if row else None

    def list(self, *, status: str | None = None, limit: int = 100) -> list[ApprovalRecord]:
        self._expire_due()
        if status is not None and status not in APPROVAL_STATES:
            raise ApprovalValidationError("unsupported approval status", details={"status": status})
        if limit < 1 or limit > 1000:
            raise ApprovalValidationError("approval list limit must be between 1 and 1000")
        if status is None:
            rows = self.connection.execute(
                "SELECT approval_id, subject, status, request_event, requested_at, expires_at, "
                "decision_event, operator_id, decision_operation_key, decided_at "
                "FROM approvals ORDER BY requested_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT approval_id, subject, status, request_event, requested_at, expires_at, "
                "decision_event, operator_id, decision_operation_key, decided_at "
                "FROM approvals WHERE status = ? ORDER BY requested_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def decide(
        self,
        approval_id: str,
        *,
        decision: str,
        operator_id: str,
        value: Any = None,
        operation_key: str | None = None,
    ) -> ApprovalRecord:
        if decision not in APPROVAL_DECISIONS:
            raise ApprovalValidationError(
                "approval decision must be approved or rejected",
                details={"decision": decision},
            )
        if not operator_id.strip():
            raise ApprovalValidationError("operator identity is required")
        record = self.get(approval_id)
        if record is None:
            raise ApprovalNotFound("approval not found", details={"approval_id": approval_id})
        if record.status == "expired":
            raise ApprovalConflict(
                "approval request has expired", details={"approval_id": approval_id}
            )
        if record.status in APPROVAL_DECISIONS:
            if (
                record.status == decision
                and operation_key is not None
                and record.decision_operation_key == operation_key
            ):
                return record
            raise ApprovalConflict(
                "approval already has a terminal decision",
                details={"approval_id": approval_id, "status": record.status},
            )
        if operation_key:
            existing = self.connection.execute(
                "SELECT approval_id FROM approvals WHERE decision_operation_key = ?",
                (operation_key,),
            ).fetchone()
            if existing and existing[0] != approval_id:
                raise ApprovalConflict(
                    "idempotency key belongs to another approval",
                    details={"operation_key": operation_key},
                )
        now = time.time()
        event = EventEnvelope(
            id=operation_key or str(uuid.uuid4()),
            source="urn:open-workflow-agent:approvals",
            type=APPROVAL_DECISION_EVENT,
            time=_timestamp(now),
            subject=record.subject,
            datacontenttype="application/json",
            data={
                "approval_id": approval_id,
                "decision": decision,
                "operator_id": operator_id,
                "value": value,
            },
            extensions={"approvalid": approval_id},
        )
        record.status = decision
        record.operator_id = operator_id
        record.decision_operation_key = operation_key
        record.decision_event_json = event.raw()
        record.decided_at = now
        self.connection.execute(
            "UPDATE approvals SET status = ?, decision_event = ?, operator_id = ?, "
            "decision_operation_key = ?, decided_at = ? WHERE approval_id = ?",
            (
                record.status,
                record.decision_event_json,
                record.operator_id,
                record.decision_operation_key,
                record.decided_at,
                record.approval_id,
            ),
        )
        self.connection.commit()
        return record

    def decision_for_strategy(self, strategy: Mapping[str, Any]) -> EventEnvelope | None:
        selected = strategy.get("one")
        event_filter = selected.get("with") if isinstance(selected, Mapping) else None
        if not isinstance(event_filter, Mapping):
            return None
        if event_filter.get("type") != APPROVAL_DECISION_EVENT:
            return None
        subject = event_filter.get("subject")
        if not isinstance(subject, str) or not subject:
            raise ApprovalValidationError(
                "durable approval listeners require a stable subject equal to the approval id"
            )
        record = self.get(subject)
        if record is None or record.status not in APPROVAL_DECISIONS:
            return None
        decision = record.decision_event
        if decision is None:
            return None
        if all(decision.get(key) == expected for key, expected in event_filter.items()):
            return _envelope(decision)
        return None

    def _expire_due(self) -> None:
        now = time.time()
        self.connection.execute(
            "UPDATE approvals SET status = 'expired' WHERE status = 'pending' "
            "AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        self.connection.commit()

    @staticmethod
    def _decode(row: Any) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row[0],
            subject=row[1],
            status=row[2],
            request_event_json=row[3],
            requested_at=float(row[4]),
            expires_at=float(row[5]) if row[5] is not None else None,
            decision_event_json=row[6],
            operator_id=row[7],
            decision_operation_key=row[8],
            decided_at=float(row[9]) if row[9] is not None else None,
        )

    def close(self) -> None:
        self.connection.close()


class ApprovalEventBus:
    """Decorate the normal bus with durable approval request/decision replay."""

    durable = False
    approval_durable = True

    def __init__(self, delegate: EventBus, store: ApprovalStore) -> None:
        self.delegate = delegate
        self.store = store

    async def publish(self, properties: Mapping[str, Any], *, default_source: str) -> EventEnvelope:
        if properties.get("type") == APPROVAL_DECISION_EVENT:
            raise ValueError("approval decisions must be submitted through the approval API")
        envelope = await self.delegate.publish(properties, default_source=default_source)
        if envelope.type == APPROVAL_REQUEST_EVENT:
            self.store.create_request(envelope)
        return envelope

    async def publish_decision(self, event: Mapping[str, Any]) -> EventEnvelope:
        return await self.delegate.publish(
            event, default_source="urn:open-workflow-agent:approvals"
        )

    async def receive(self, strategy: Mapping[str, Any]) -> EventEnvelope:
        replay = self.store.decision_for_strategy(strategy)
        if replay is not None:
            return replay
        return await self.delegate.receive(strategy)


class ApprovalService:
    """Operator-facing approval service with a bounded static bearer guard."""

    def __init__(
        self,
        database: str | Path,
        *,
        enabled: bool,
        operator_security_profile: str | None,
        security: SecurityConfig,
        event_bus: EventBus,
    ) -> None:
        self.enabled = enabled
        self.operator_security_profile = operator_security_profile
        self.security = security
        self.store = ApprovalStore(database)
        self.event_bus: EventBus = ApprovalEventBus(event_bus, self.store) if enabled else event_bus

    def ensure_enabled(self) -> None:
        if not self.enabled:
            raise ApprovalAuthorizationError("durable approvals are not enabled")

    def authorize(self, authorization: str | None, operator_id: str | None) -> str:
        self.ensure_enabled()
        if not self.operator_security_profile:
            raise ApprovalAuthorizationError("approval operator authorization is not configured")
        try:
            profile = self.security.profile(self.operator_security_profile)
        except ValueError as exc:
            raise ApprovalAuthorizationError("approval operator authorization failed") from exc
        if not isinstance(profile, BearerSecurityProfile):
            raise ApprovalAuthorizationError("approval operator authorization failed")
        if not authorization or not authorization.startswith("Bearer "):
            raise ApprovalAuthorizationError("approval operator bearer token is required")
        supplied = authorization.removeprefix("Bearer ").strip()
        try:
            expected = resolve_secret(profile.token)
        except ValueError as exc:
            raise ApprovalAuthorizationError("approval operator authorization failed") from exc
        if not hmac.compare_digest(supplied, expected):
            raise ApprovalAuthorizationError("approval operator authorization failed")
        if operator_id is None or not operator_id.strip():
            raise ApprovalAuthorizationError("approval operator identity is required")
        return operator_id.strip()

    async def decide(
        self,
        approval_id: str,
        *,
        decision: str,
        operator_id: str,
        value: Any,
        operation_key: str | None,
    ) -> ApprovalRecord:
        self.ensure_enabled()
        record = self.store.decide(
            approval_id,
            decision=decision,
            operator_id=operator_id,
            value=value,
            operation_key=operation_key,
        )
        assert record.decision_event is not None
        event_bus = self.event_bus
        if not isinstance(event_bus, ApprovalEventBus):
            raise ApprovalAuthorizationError("durable approval event delivery is unavailable")
        await event_bus.publish_decision(record.decision_event)
        return record

    def capabilities(self) -> dict[str, Any]:
        return {
            "approval": self.enabled,
            "durable": self.enabled,
            "replay": self.enabled,
            "operatorAuthorization": "bearer" if self.enabled else None,
        }

    def close(self) -> None:
        self.store.close()


def _parse_event_time(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def _parse_expiry(value: Any) -> float | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApprovalValidationError("approvalexpiresat must be an RFC 3339 string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise ApprovalValidationError("approvalexpiresat must be an RFC 3339 string") from exc


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")


def _envelope(value: Mapping[str, Any]) -> EventEnvelope:
    known = {
        "id",
        "source",
        "type",
        "time",
        "subject",
        "datacontenttype",
        "dataschema",
        "data",
    }
    return EventEnvelope(
        id=str(value["id"]),
        source=str(value["source"]),
        type=str(value["type"]),
        time=str(value["time"]),
        subject=value.get("subject") if isinstance(value.get("subject"), str) else None,
        datacontenttype=(
            value.get("datacontenttype") if isinstance(value.get("datacontenttype"), str) else None
        ),
        dataschema=value.get("dataschema") if isinstance(value.get("dataschema"), str) else None,
        data=value.get("data"),
        extensions={key: item for key, item in value.items() if key not in known},
    )
