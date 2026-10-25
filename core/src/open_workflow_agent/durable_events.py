"""Async delivery adapter over the embedded immutable event store."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .effects import EffectIdentity, EffectReceipt
from .event_store import EventStore
from .events import EventDecision, EventEnvelope, EventProgress, EventRegistration
from .events import _matches as matches_one
from .ownership import ExecutionLease
from .storage import StorageConnection

EventParticipant = Callable[[StorageConnection, EventEnvelope], None]
CandidateEvaluator = Callable[[EventEnvelope, EventProgress], EventDecision | None]
DEFAULT_EVENT_SCOPE = "anonymous"


class DurableEventBus:
    """Database rows are authoritative; polling supports separate processes.

    No task or subscription state is copied from a native SDK. A process-level
    coroutine cancellation leaves registrations recoverable; authenticated
    invocation termination explicitly closes them through the common runtime.
    """

    durable = True

    def __init__(self, store: EventStore, *, poll_interval_seconds: float = 0.05) -> None:
        self.store = store
        self.poll_interval_seconds = poll_interval_seconds

    async def publish(self, properties: Mapping[str, Any], *, default_source: str) -> EventEnvelope:
        return await self.publish_scoped(
            properties, default_source=default_source, scope=DEFAULT_EVENT_SCOPE
        )

    async def publish_scoped(
        self,
        properties: Mapping[str, Any],
        *,
        default_source: str,
        scope: str,
        participant: EventParticipant | None = None,
    ) -> EventEnvelope:
        return self.store.ingest(
            properties, default_source=default_source, scope=scope, participant=participant
        )

    def receive(self, strategy: Mapping[str, Any]) -> Awaitable[EventEnvelope]:
        """Direct consumers retain the public single-envelope bus contract.

        Workflow recovery uses an explicit durable registration instead. No
        synthetic invocation, journal receipt, or SDK state is created here.
        """
        captured = dict(strategy)

        async def wait() -> EventEnvelope:
            cursor = 0
            while True:
                self.store.deliver_outbox(limit=self.store.limits.scan_batch)
                for sequence, event in self.store.arrivals(scope=DEFAULT_EVENT_SCOPE, after=cursor):
                    cursor = sequence
                    if matches_one(event, captured):
                        return event
                await asyncio.sleep(self.poll_interval_seconds)

        return wait()

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
        participant: EventParticipant | None = None,
    ) -> EffectReceipt:
        return self.store.publish_effect(
            identity,
            request_digest,
            properties,
            default_source=default_source,
            scope=scope,
            execution_lease=execution_lease,
            operation_id=operation_id,
            participant=participant,
        )

    def register_workflow_listener(
        self,
        identity: EffectIdentity,
        strategy: Mapping[str, Any],
        *,
        scope: str,
        execution_lease: ExecutionLease | None,
    ) -> EventRegistration:
        return self.store.register(identity, strategy, scope=scope, execution_lease=execution_lease)

    def listener_state(self, registration: EventRegistration) -> EventProgress:
        return self.store.state(registration)

    def complete_workflow_listener(
        self, registration: EventRegistration, *, execution_lease: ExecutionLease | None
    ) -> EventProgress:
        return self.store.complete(registration, execution_lease=execution_lease)

    def receive_next(
        self,
        registration: EventRegistration,
        *,
        execution_lease: ExecutionLease | None,
        evaluate_candidate: CandidateEvaluator,
    ) -> Awaitable[EventProgress]:
        previous_ordinal = self.store.state(registration).decision_ordinal

        async def wait() -> EventProgress:
            while True:
                self.store.deliver_outbox(limit=self.store.limits.scan_batch)
                progress = self.store.select(
                    registration,
                    execution_lease=execution_lease,
                    evaluate_candidate=evaluate_candidate,
                )
                if (
                    progress.decision_ordinal > previous_ordinal
                    or progress.selected is not None
                    or progress.completed
                    or progress.closed
                ):
                    return progress
                await asyncio.sleep(self.poll_interval_seconds)

        return wait()

    async def receive_registered(
        self,
        registration: EventRegistration,
        *,
        execution_lease: ExecutionLease | None,
        evaluate_candidate: CandidateEvaluator,
    ) -> EffectReceipt:
        while True:
            progress = await self.receive_next(
                registration,
                execution_lease=execution_lease,
                evaluate_candidate=evaluate_candidate,
            )
            if progress.receipt is not None:
                return progress.receipt
            if progress.closed:
                raise asyncio.CancelledError

    def abandon_workflow_listener(
        self, registration: EventRegistration, *, execution_lease: ExecutionLease | None
    ) -> None:
        self.store.abandon(registration, execution_lease=execution_lease)

    def verify_invocation(self, invocation_id: str, fingerprint: str) -> None:
        self.store.verify_invocation(invocation_id, fingerprint)
