"""Bounded delivery of accepted sandbox jobs through the common manager.

The daemon's cancellation releases observation ownership; it never cancels an
independent executable job. No backend is enabled or launched by this module.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections import deque
from contextlib import suppress
from typing import Literal, cast

from ..errors import InvocationStateConflict, SandboxPolicyError
from .job_backend import SandboxJobLaunch, SandboxJobObservation
from .jobs import SandboxJob, SandboxJobBackendBinding, SandboxJobLease, SandboxJobStore
from .manager import SandboxManager


class SandboxJobDispatcher:
    def __init__(
        self,
        store: SandboxJobStore,
        manager: SandboxManager,
        *,
        lease_seconds: float = 30,
        io_timeout: float = 10,
        poll_seconds: float = 0.25,
        scan_limit: int = 100,
    ) -> None:
        if (
            any(
                not math.isfinite(value) or value <= 0
                for value in (lease_seconds, io_timeout, poll_seconds)
            )
            or lease_seconds > 3600
            or io_timeout > lease_seconds / 2
            or poll_seconds > 60
            or type(scan_limit) is not int
            or not 1 <= scan_limit <= 1000
        ):
            raise ValueError("sandbox job delivery bounds are invalid")
        self.store = store
        self.manager = manager
        self.lease_seconds = lease_seconds
        self.io_timeout = io_timeout
        self.poll_seconds = poll_seconds
        self.scan_limit = scan_limit
        self._cursor: str | None = None
        self._errors: deque[str] = deque(maxlen=128)
        self._task: asyncio.Task[None] | None = None

    @property
    def errors(self) -> tuple[str, ...]:
        # Keep bounded diagnostic categories, never a transport/secret message.
        return tuple(self._errors)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as error:
                # A failed bounded scan grants no execution authority. Retain
                # only its category and let later polls inspect restored proof.
                self._errors.append(type(error).__name__)
            await asyncio.sleep(self.poll_seconds)

    async def tick(self) -> None:
        identifiers = self.store.pending(limit=self.scan_limit, after=self._cursor)
        if not identifiers:
            self._cursor = None
        for identifier in identifiers:
            self._cursor = identifier
            try:
                await self.deliver(identifier)
            except Exception as error:
                self._errors.append(type(error).__name__)

    def _current(self, lease: SandboxJobLease) -> tuple[SandboxJob, SandboxJobLaunch]:
        job = self.store.verify(lease)
        launch = SandboxJobLaunch.from_verified_job(job, lease)
        self.manager.validate_job_profile(launch.profile)
        return job, launch

    @staticmethod
    def _binding(job: SandboxJob) -> SandboxJobBackendBinding | None:
        return (
            SandboxJobBackendBinding(**json.loads(job.backend_json)["binding"])
            if job.backend_json is not None
            else None
        )

    def _record(self, lease: SandboxJobLease, observation: SandboxJobObservation) -> None:
        if observation.state == "unknown":
            self.store.mark_uncertain(lease, reason="outcome_unavailable")
            return
        if observation.binding is None:
            raise SandboxPolicyError("sandbox job observation has no backend identity")
        self.store.record_accepted(lease, observation.binding)
        if observation.control_ack_generation is not None:
            self.store.acknowledge_control(
                lease, observation.binding, generation=observation.control_ack_generation
            )
        if observation.state == "terminal":
            if observation.outcome is None:
                raise SandboxPolicyError("sandbox job terminal observation has no known outcome")
            if observation.original_deadline is not None:
                self.store.record_original_deadline(
                    lease,
                    observation.binding,
                    observation.outcome,
                    proof=observation.original_deadline,
                )
            self.store.record_terminal(
                lease, observation.binding, observation.outcome, reason=observation.reason
            )

    async def deliver(self, identifier: str) -> None:
        # Claim revalidates the original archive, independent admission and
        # current deployment before ownership and all backend communication.
        lease = self.store.claim(identifier, seconds=self.lease_seconds)
        submitting = False
        try:
            # Each store operation commits/releases its own fence before
            # yielding; parent execution heartbeats must not be starved by
            # several consecutive physical-profile verification stages.
            await asyncio.sleep(0)
            job = self.store.get(identifier)
            await asyncio.sleep(0)
            if job is None or job.status == "terminal":
                return
            admission = json.loads(job.admission_json)
            self.manager.validate_job_profile_from_admission(admission)
            if self.store._now() >= admission["deadline_at"] and job.control_json is None:
                self.store.request_control(lease, reason="deadline")
                await asyncio.sleep(0)
                job = self.store.get(identifier)
                await asyncio.sleep(0)
                if job is None:
                    raise SandboxPolicyError("sandbox job disappeared during deadline control")
            if job.status == "ready" and job.control_json is not None:
                self.store.settle_unstarted_control(lease)
                return
            if job.status == "ready":
                self.store.begin_submit(lease)
                submitting = True
                await asyncio.sleep(0)
            lease = self.store.renew(lease, seconds=self.lease_seconds)
            await asyncio.sleep(0)
            job, launch = self._current(lease)
            await asyncio.sleep(0)
            async with asyncio.timeout(self.io_timeout):
                observation = (
                    await self.manager.submit_job(launch)
                    if submitting
                    else await self.manager.inspect_job(launch, self._binding(job))
                )
            self._record(lease, observation)
            await asyncio.sleep(0)
            job = self.store.get(identifier)
            await asyncio.sleep(0)
            if job is None or job.status == "terminal" or job.control_json is None:
                return
            binding = self._binding(job)
            if binding is None:
                return
            control = json.loads(job.control_json)
            lease = self.store.renew(lease, seconds=self.lease_seconds)
            await asyncio.sleep(0)
            job, launch = self._current(lease)
            await asyncio.sleep(0)
            async with asyncio.timeout(self.io_timeout):
                observation = await self.manager.cancel_job(
                    launch,
                    binding,
                    generation=control["generation"],
                    reason=cast(Literal["cancel", "deadline"], control["reason"]),
                )
            self._record(lease, observation)
        except asyncio.CancelledError:
            # A dispatched intent remains durable even when its reply is lost.
            raise
        except (TimeoutError, OSError):
            self.store.mark_uncertain(
                lease, reason="submit_response_lost" if submitting else "outcome_unavailable"
            )
        finally:
            with suppress(InvocationStateConflict):
                self.store.release(lease)
