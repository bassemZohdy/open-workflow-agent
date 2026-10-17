"""Private trusted backend SPI for independently owned sandbox jobs.

These descriptors grant no launch authority by themselves. An implementation
must validate the original deployment profile and live common job generation
before IPC or process mutation. Only SandboxManager exposes this boundary.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from ..errors import SandboxPolicyError
from .contract import SandboxExecutionRequest, SandboxExecutionResult, SandboxSecretReference
from .jobs import SandboxJob, SandboxJobBackendBinding, SandboxJobLease, SandboxJobProfile


@dataclass(frozen=True, slots=True)
class SandboxJobLaunch:
    lease: SandboxJobLease
    profile: SandboxJobProfile
    request: SandboxExecutionRequest
    request_digest: str
    deadline_at: float
    submit_id: str

    @classmethod
    def from_verified_job(cls, job: SandboxJob, lease: SandboxJobLease) -> SandboxJobLaunch:
        """Use only a store-verified record under its current independent lease."""
        if job.submit_json is None or job.admission_digest != lease.admission_digest:
            raise SandboxPolicyError("sandbox job has no bound submission intent")
        admission = json.loads(job.admission_json)
        submit = json.loads(job.submit_json)
        request = dict(admission["request"])
        request["arguments"] = tuple(request["arguments"])
        request["environment"] = tuple(
            (name, SandboxSecretReference(value["secret_reference"]))
            for name, value in request["environment"].items()
        )
        return cls(
            lease=lease,
            profile=SandboxJobProfile(**admission["backend_profile"]),
            request=SandboxExecutionRequest(**request),
            request_digest=admission["request_digest"],
            deadline_at=admission["deadline_at"],
            submit_id=submit["submit_id"],
        )


@dataclass(frozen=True, slots=True)
class SandboxJobOriginalDeadlineProof:
    """Trusted backend's verified terminal cause, independent of later control."""

    admission_digest: str
    deadline_at: float
    observation_digest: str

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)
                for value in (self.admission_digest, self.observation_digest)
            )
            or type(self.deadline_at) not in {int, float}
            or not math.isfinite(self.deadline_at)
            or self.deadline_at <= 0
        ):
            raise SandboxPolicyError("invalid original sandbox deadline proof")


@dataclass(frozen=True, slots=True)
class SandboxJobObservation:
    state: Literal["accepted", "running", "terminal", "unknown"]
    binding: SandboxJobBackendBinding | None = None
    outcome: SandboxExecutionResult | None = None
    reason: Literal["exited", "cancel", "deadline"] = "exited"
    control_ack_generation: int | None = None
    original_deadline: SandboxJobOriginalDeadlineProof | None = None

    def __post_init__(self) -> None:
        if self.state not in {"accepted", "running", "terminal", "unknown"}:
            raise SandboxPolicyError("invalid sandbox job observation")
        if self.state == "unknown":
            valid = self.outcome is None and self.control_ack_generation is None
        elif self.state == "terminal":
            valid = self.binding is not None and self.outcome is not None
        else:
            valid = self.binding is not None and self.outcome is None
        if (
            not valid
            or self.reason not in {"exited", "cancel", "deadline"}
            or self.control_ack_generation is not None
            and (type(self.control_ack_generation) is not int or self.control_ack_generation < 1)
            or self.original_deadline is not None
            and (
                not isinstance(self.original_deadline, SandboxJobOriginalDeadlineProof)
                or self.state != "terminal"
                or self.reason != "deadline"
                or self.outcome is None
            )
        ):
            raise SandboxPolicyError("inconsistent sandbox job observation")


class SandboxJobBackend(Protocol):
    def profile(self) -> SandboxJobProfile: ...

    async def submit(self, launch: SandboxJobLaunch) -> SandboxJobObservation:
        """Called once after the durable intent, never as an uncertain retry."""
        ...

    async def inspect(
        self, launch: SandboxJobLaunch, binding: SandboxJobBackendBinding | None
    ) -> SandboxJobObservation:
        """Inspect exact submitted identity even when the acceptance reply was lost."""
        ...

    async def cancel(
        self,
        launch: SandboxJobLaunch,
        binding: SandboxJobBackendBinding,
        *,
        generation: int,
        reason: Literal["cancel", "deadline"],
    ) -> SandboxJobObservation:
        """Apply the persisted explicit control, fencing the live owner again."""
        ...
