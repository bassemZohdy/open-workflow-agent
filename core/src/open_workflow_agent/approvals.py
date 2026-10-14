"""Bounded durable human-approval state composed with portable workflow events."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .durable_events import DEFAULT_EVENT_SCOPE, DurableEventBus
from .effects import EffectIdentity, EffectReceipt
from .errors import (
    ApprovalAuthorizationError,
    ApprovalConflict,
    ApprovalNotFound,
    ApprovalValidationError,
    OwaError,
)
from .events import EventBus, EventDecision, EventEnvelope, EventProgress, EventRegistration
from .ownership import ExecutionLease
from .persistence import InvocationStore
from .protected_approval_policy import actor_identity
from .protected_approval_store import ProtectedApprovalStore, _tables, table_exists
from .security import BearerSecurityProfile, Principal, SecurityConfig, authenticate_static
from .storage import StorageConnection, ensure_storage_namespace, open_storage

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
    event_scope: str = DEFAULT_EVENT_SCOPE
    assignment_revision: int | None = None
    assignee: str | None = None
    audit: tuple[dict[str, Any], ...] = ()

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
        if self.assignment_revision is not None:
            value["revision"] = self.assignment_revision
            value["assignee"] = self.assignee
            value["audit"] = list(self.audit)
        return value


class ApprovalStore:
    """Durable common approval metadata; contains no engine-native state."""

    def __init__(
        self, database: str | Path = ":memory:", *, connection: StorageConnection | None = None
    ) -> None:
        self._owns_connection = connection is None
        self.connection = connection or open_storage(database, "owa_approvals")
        if connection is not None and connection.is_postgresql:
            ensure_storage_namespace(str(database), "owa_approvals")
        self._table = "owa_approvals.approvals" if self.connection.is_postgresql else "approvals"
        self.protected: ProtectedApprovalStore | None = None
        self._execute(
            """CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            status TEXT NOT NULL,
            request_event TEXT NOT NULL,
            requested_at DOUBLE PRECISION NOT NULL,
            expires_at DOUBLE PRECISION,
            decision_event TEXT,
            operator_id TEXT,
            decision_operation_key TEXT UNIQUE,
            decided_at DOUBLE PRECISION)"""
        )
        self.connection.commit()
        if self.connection.is_postgresql:
            columns = self.connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'owa_approvals' AND table_name = 'approvals'"
            ).fetchall()
            names = {row[0] for row in columns}
            legacy_time_columns = {
                row[0]
                for row in self.connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'owa_approvals' AND table_name = 'approvals' "
                    "AND data_type = 'real' AND column_name IN "
                    "('requested_at', 'expires_at', 'decided_at')"
                ).fetchall()
            }
            for name in ("requested_at", "expires_at", "decided_at"):
                if name not in legacy_time_columns:
                    continue
                # PostgreSQL REAL is float32. Persisted epoch timestamps require
                # float64 precision to preserve exact protected-approval proofs.
                self.connection.execute(
                    f"ALTER TABLE {self._table} ALTER COLUMN {name} "
                    f"TYPE DOUBLE PRECISION USING {name}::double precision"
                )
        else:
            names = {
                row[1] for row in self.connection.execute("PRAGMA table_info(approvals)").fetchall()
            }
        if "event_scope" not in names:
            self._execute(
                "ALTER TABLE approvals ADD COLUMN event_scope TEXT NOT NULL DEFAULT 'anonymous'"
            )
            self.connection.commit()

    def _execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> Any:
        # Namespace is a fixed internal identifier, never a caller value.
        return self.connection.execute(
            re.sub(r"(?<![\w.])approvals(?!\w)", self._table, statement), parameters
        )

    def create_request(
        self,
        event: EventEnvelope,
        *,
        scope: str = DEFAULT_EVENT_SCOPE,
        commit: bool = True,
        origin_identity: EffectIdentity | None = None,
        request_digest: str | None = None,
    ) -> ApprovalRecord:
        if event.type != APPROVAL_REQUEST_EVENT:
            raise ApprovalValidationError(
                "approval request must use the reserved request event type"
            )
        if (
            isinstance(event.data, dict)
            and "approvalPolicy" in event.data
            and (self.protected is None or origin_identity is None or request_digest is None)
        ):
            raise ApprovalValidationError("protected approvals require an owned workflow request")
        if not event.subject or event.subject != event.id:
            raise ApprovalValidationError(
                "durable approval requests require subject to equal the stable approval id",
                details={"approval_id": event.id, "subject": event.subject},
            )
        existing = self.get(event.id, commit=commit)
        request_json = event.raw()
        if existing is not None:
            if existing.request_event_json != request_json or existing.event_scope != scope:
                raise ApprovalConflict(
                    "approval id is already bound to a different request",
                    details={"approval_id": event.id},
                )
            if isinstance(event.data, dict) and "approvalPolicy" in event.data:
                if self.protected is None:
                    raise ApprovalValidationError("protected approvals are not enabled")
                self.protected.admit(existing, event, origin_identity, request_digest)
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
            event_scope=scope,
        )
        self._execute(
            "INSERT INTO approvals(approval_id, subject, status, request_event, requested_at, "
            "expires_at, decision_event, operator_id, decision_operation_key, "
            "decided_at, event_scope) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                record.event_scope,
            ),
        )
        if isinstance(event.data, dict) and "approvalPolicy" in event.data:
            if self.protected is None:
                raise ApprovalValidationError("protected approvals are not enabled")
            self.protected.admit(record, event, origin_identity, request_digest)
        if commit:
            self.connection.commit()
        return record

    def get(self, approval_id: str, *, commit: bool = True) -> ApprovalRecord | None:
        original = self._execute(
            "SELECT request_event FROM approvals WHERE approval_id=?", (approval_id,)
        ).fetchone()
        protected_request = (
            original is not None
            and isinstance(data := json.loads(original[0]).get("data"), dict)
            and "approvalPolicy" in data
        )
        if not protected_request:
            self._expire_due(commit=commit)
        lock = " FOR UPDATE" if self.connection.is_postgresql and not commit else ""
        row = self._execute(
            "SELECT approval_id, subject, status, request_event, requested_at, expires_at, "
            "decision_event, operator_id, decision_operation_key, decided_at, event_scope "
            "FROM approvals WHERE approval_id = ?" + lock,
            (approval_id,),
        ).fetchone()
        if row is None:
            return None
        record = self._decode(row)
        data = record.request_event.get("data")
        if isinstance(data, dict) and "approvalPolicy" in data:
            if self.protected is None:
                raise ApprovalAuthorizationError("protected approval authority is unavailable")
            _, state = self.protected.inspect(approval_id)
            record.assignment_revision, record.assignee = state[:2]
            record.audit = tuple(json.loads(item[1]) for item in state[3])
        return record

    def list(self, *, status: str | None = None, limit: int = 100) -> list[ApprovalRecord]:
        self._expire_due()
        if status is not None and status not in APPROVAL_STATES:
            raise ApprovalValidationError("unsupported approval status", details={"status": status})
        if limit < 1 or limit > 1000:
            raise ApprovalValidationError("approval list limit must be between 1 and 1000")
        if status is None:
            rows = self._execute(
                "SELECT approval_id, subject, status, request_event, requested_at, expires_at, "
                "decision_event, operator_id, decision_operation_key, decided_at, event_scope "
                "FROM approvals ORDER BY requested_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT approval_id, subject, status, request_event, requested_at, expires_at, "
                "decision_event, operator_id, decision_operation_key, decided_at, event_scope "
                "FROM approvals WHERE status = ? ORDER BY requested_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def status_counts(self) -> dict[str, int]:
        self._expire_due()
        rows = self._execute("SELECT status, COUNT(*) FROM approvals GROUP BY status").fetchall()
        return {str(status): int(count) for status, count in rows}

    def decide(
        self,
        approval_id: str,
        *,
        decision: str,
        operator_id: str,
        value: Any = None,
        operation_key: str | None = None,
        commit: bool = True,
    ) -> ApprovalRecord:
        if decision not in APPROVAL_DECISIONS:
            raise ApprovalValidationError(
                "approval decision must be approved or rejected",
                details={"decision": decision},
            )
        if not operator_id.strip():
            raise ApprovalValidationError("operator identity is required")
        record = self.get(approval_id, commit=commit)
        if record is None:
            raise ApprovalNotFound("approval not found", details={"approval_id": approval_id})
        if record.assignment_revision is not None:
            raise ApprovalValidationError(
                "protected approval requires its atomic protected transaction"
            )
        if record.status == "expired":
            raise ApprovalConflict(
                "approval request has expired", details={"approval_id": approval_id}
            )
        if record.status in APPROVAL_DECISIONS:
            if (
                record.status == decision
                and operation_key is not None
                and record.decision_operation_key == operation_key
                and record.operator_id == operator_id
                and record.decision_event is not None
                and record.decision_event.get("data", {}).get("value") == value
            ):
                return record
            raise ApprovalConflict(
                "approval already has a terminal decision",
                details={"approval_id": approval_id, "status": record.status},
            )
        if operation_key:
            existing = self._execute(
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
        changed = self._execute(
            "UPDATE approvals SET status = ?, decision_event = ?, operator_id = ?, "
            "decision_operation_key = ?, decided_at = ? WHERE approval_id = ? "
            "AND status = 'pending'",
            (
                record.status,
                record.decision_event_json,
                record.operator_id,
                record.decision_operation_key,
                record.decided_at,
                record.approval_id,
            ),
        ).rowcount
        if changed != 1:
            raise ApprovalConflict("approval terminal outcome changed concurrently")
        if commit:
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
        if (
            record is None
            or record.status not in APPROVAL_DECISIONS
            or record.event_scope != DEFAULT_EVENT_SCOPE
        ):
            return None
        decision = record.decision_event
        if decision is None:
            return None
        if all(decision.get(key) == expected for key, expected in event_filter.items()):
            return _envelope(decision)
        return None

    def matches_committed_decision(self, selected: Mapping[str, Any], event: EventEnvelope) -> bool:
        """Read-only verification inside the event store's owned transaction."""
        properties = selected.get("with")
        if not isinstance(properties, Mapping) or properties.get("type") != APPROVAL_DECISION_EVENT:
            return True
        if event.type != APPROVAL_DECISION_EVENT or not event.subject:
            return False
        declared_subject = properties.get("subject")
        subject = (
            declared_subject
            if isinstance(declared_subject, str) and "${" not in declared_subject
            else event.subject
        )
        row = self._execute(
            "SELECT status, decision_event, subject FROM approvals WHERE approval_id = ?",
            (subject,),
        ).fetchone()
        return bool(
            row is not None
            and row[0] in APPROVAL_DECISIONS | {"expired"}
            and row[1] == event.raw()
            and row[2] == event.subject
        )

    def _expire_due(self, *, commit: bool = True) -> None:
        now = time.time()
        protected, _ = _tables(self.connection)
        condition = (
            f" AND approval_id NOT IN (SELECT approval_id FROM {protected})"
            if table_exists(self.connection, protected)
            else ""
        )
        self._execute(
            "UPDATE approvals SET status = 'expired' WHERE status = 'pending' "
            "AND expires_at IS NOT NULL AND expires_at <= ? "
            "AND request_event NOT LIKE ?" + condition,
            (now, '%"approvalPolicy"%'),
        )
        if commit:
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
            event_scope=row[10],
        )

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()


class ApprovalEventBus:
    """Decorate the normal bus with durable approval request/decision replay."""

    approval_durable = True

    def __init__(self, delegate: EventBus, store: ApprovalStore) -> None:
        self.delegate = delegate
        self.store = store
        self.durable = delegate.durable

    def event_filter_allowed(self, selected: Mapping[str, Any], event: EventEnvelope) -> bool:
        return self.store.matches_committed_decision(selected, event)

    async def publish(self, properties: Mapping[str, Any], *, default_source: str) -> EventEnvelope:
        return await self.publish_scoped(
            properties, default_source=default_source, scope=DEFAULT_EVENT_SCOPE
        )

    async def publish_scoped(
        self, properties: Mapping[str, Any], *, default_source: str, scope: str
    ) -> EventEnvelope:
        if properties.get("type") == APPROVAL_DECISION_EVENT:
            raise ValueError("approval decisions must be submitted through the approval API")
        data = properties.get("data")
        if (
            properties.get("type") == APPROVAL_REQUEST_EVENT
            and isinstance(data, Mapping)
            and "approvalPolicy" in data
        ):
            raise ApprovalValidationError("protected approvals require an owned workflow request")
        if isinstance(self.delegate, DurableEventBus):
            return await self.delegate.publish_scoped(
                properties,
                default_source=default_source,
                scope=scope,
                participant=lambda _connection, event: self._request_participant(event, scope),
            )
        envelope = await self.delegate.publish(properties, default_source=default_source)
        if envelope.type == APPROVAL_REQUEST_EVENT:
            self.store.create_request(envelope, scope=scope)
        return envelope

    def _request_participant(
        self,
        event: EventEnvelope,
        scope: str,
        origin_identity: EffectIdentity | None = None,
        request_digest: str | None = None,
    ) -> None:
        if event.type == APPROVAL_REQUEST_EVENT:
            self.store.create_request(
                event,
                scope=scope,
                commit=False,
                origin_identity=origin_identity,
                request_digest=request_digest,
            )

    async def publish_workflow_event(
        self,
        identity: EffectIdentity,
        request_digest: str,
        properties: Mapping[str, Any],
        *,
        default_source: str,
        scope: str,
        execution_lease: ExecutionLease | None,
        operation_id: str | None = None,
    ) -> EffectReceipt:
        if properties.get("type") == APPROVAL_DECISION_EVENT:
            raise ValueError("approval decisions must be submitted through the approval API")
        delegate = self._durable_delegate()
        return await delegate.publish_workflow_event(
            identity,
            request_digest,
            properties,
            default_source=default_source,
            scope=scope,
            execution_lease=execution_lease,
            operation_id=operation_id,
            participant=lambda _connection, event: self._request_participant(
                event, scope, identity, request_digest
            ),
        )

    def _durable_delegate(self) -> DurableEventBus:
        if not isinstance(self.delegate, DurableEventBus):
            raise ApprovalValidationError("durable generic event delivery is unavailable")
        return self.delegate

    def register_workflow_listener(
        self,
        identity: EffectIdentity,
        strategy: Mapping[str, Any],
        *,
        scope: str,
        execution_lease: ExecutionLease | None,
    ) -> EventRegistration:
        return self._durable_delegate().register_workflow_listener(
            identity, strategy, scope=scope, execution_lease=execution_lease
        )

    def listener_state(self, registration: EventRegistration) -> EventProgress:
        return self._durable_delegate().listener_state(registration)

    def complete_workflow_listener(
        self, registration: EventRegistration, *, execution_lease: ExecutionLease | None
    ) -> EventProgress:
        return self._durable_delegate().complete_workflow_listener(
            registration, execution_lease=execution_lease
        )

    async def receive_next(
        self,
        registration: EventRegistration,
        *,
        execution_lease: ExecutionLease | None,
        evaluate_candidate: Callable[[EventEnvelope, EventProgress], EventDecision | None],
    ) -> EventProgress:
        return await self._durable_delegate().receive_next(
            registration, execution_lease=execution_lease, evaluate_candidate=evaluate_candidate
        )

    async def receive_registered(
        self,
        registration: EventRegistration,
        *,
        execution_lease: ExecutionLease | None,
        evaluate_candidate: Callable[[EventEnvelope, EventProgress], EventDecision | None],
    ) -> EffectReceipt:
        return await self._durable_delegate().receive_registered(
            registration, execution_lease=execution_lease, evaluate_candidate=evaluate_candidate
        )

    def abandon_workflow_listener(
        self, registration: EventRegistration, *, execution_lease: ExecutionLease | None
    ) -> None:
        self._durable_delegate().abandon_workflow_listener(
            registration, execution_lease=execution_lease
        )

    def verify_invocation(self, invocation_id: str, fingerprint: str) -> None:
        self._durable_delegate().verify_invocation(invocation_id, fingerprint)

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
        connection: StorageConnection | None = None,
        protected: bool = False,
        actor_security_profiles: list[str] | tuple[str, ...] = (),
        invocations: InvocationStore | None = None,
        max_pending: int = 1000,
        max_policy_bytes: int = 65536,
        scan_limit: int = 100,
    ) -> None:
        self.enabled = enabled
        self.operator_security_profile = operator_security_profile
        self.security = security
        self.store = ApprovalStore(database, connection=connection)
        self.actor_security_profiles = tuple(actor_security_profiles)
        self.scan_limit = scan_limit
        self._expiry_cursor: tuple[float, str] | None = None
        self._expiry_errors: deque[str] = deque(maxlen=100)
        self.protected_store: ProtectedApprovalStore | None = None
        if protected:
            if not enabled or not isinstance(event_bus, DurableEventBus) or invocations is None:
                raise ApprovalValidationError(
                    "protected approvals require common durable event storage"
                )
            actors = []
            for name in self.actor_security_profiles:
                profile = security.profile(name)
                if not isinstance(profile, BearerSecurityProfile) or not profile.principal:
                    raise ApprovalValidationError(
                        "protected actors require explicit bearer principals"
                    )
                actors.append(actor_identity(profile.principal))
            if not actors or len(set(actors)) != len(actors):
                raise ApprovalValidationError("protected actor principals must be unique")
            self.protected_store = ProtectedApprovalStore(
                self.store,
                invocations,
                actor_ids=frozenset(actors),
                max_pending=max_pending,
                max_policy_bytes=max_policy_bytes,
            )
            self.store.protected = self.protected_store
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
        try:
            if not isinstance(authorization, str):
                raise ValueError
            authenticate_static([(b"authorization", authorization.encode("ascii"))], profile)
        except (OwaError, ValueError, TypeError, UnicodeError):
            raise ApprovalAuthorizationError("approval operator authorization failed") from None
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
        revision: int | None = None,
        actor: Principal | None = None,
    ) -> ApprovalRecord:
        self.ensure_enabled()
        record = self.store.get(approval_id)
        if record is not None and record.assignment_revision is not None:
            if actor is None or actor.identity != operator_id:
                raise ApprovalAuthorizationError(
                    "protected approval requires trusted actor credentials"
                )
            return await self.mutate_protected(
                approval_id,
                action=decision,
                actor=actor,
                revision=revision,
                operation_key=operation_key,
                value=value,
            )
        if actor is not None:
            raise ApprovalAuthorizationError(
                "legacy approval requires its original operator authority"
            )
        event_bus = self.event_bus
        if isinstance(event_bus, ApprovalEventBus) and isinstance(
            event_bus.delegate, DurableEventBus
        ):
            durable = event_bus.delegate
            with durable.store.transaction():
                record = self.store.decide(
                    approval_id,
                    decision=decision,
                    operator_id=operator_id,
                    value=value,
                    operation_key=operation_key,
                    commit=False,
                )
                assert record.decision_event is not None
                durable.store.ingest_in_transaction(
                    record.decision_event,
                    default_source="urn:open-workflow-agent:approvals",
                    scope=record.event_scope,
                )
            return record
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

    def authorize_actor(
        self, authorization: str | None, operator_id: str | None = None
    ) -> Principal:
        from .protected_approval_service import authorize_actor

        return authorize_actor(self, authorization, operator_id)

    def visible_record(self, approval_id: str, actor: Principal) -> ApprovalRecord:
        from .protected_approval_service import visible_record

        return visible_record(self, approval_id, actor)

    async def mutate_protected(
        self,
        approval_id: str,
        *,
        action: str,
        actor: Principal,
        revision: int | None,
        operation_key: str | None,
        value: Any = None,
        assignee: str | None = None,
    ) -> ApprovalRecord:
        from .protected_approval_service import mutate_protected

        return await mutate_protected(
            self,
            approval_id,
            action=action,
            actor=actor,
            revision=revision,
            operation_key=operation_key,
            value=value,
            assignee=assignee,
        )

    async def expire_due(self, *, limit: int = 100) -> int:
        from .protected_approval_service import expire_due

        return await expire_due(self, limit=limit)

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
