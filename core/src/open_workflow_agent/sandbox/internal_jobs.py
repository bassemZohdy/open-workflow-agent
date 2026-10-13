"""Opt-in embedded SQLite job supervisor, exclusively behind SandboxManager."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from ..config import SandboxConfig
from ..effects import effect_request_digest
from ..errors import SandboxPolicyError
from ._job_ipc import check_path, create_key, private_directory, read_frame, read_key, write_frame
from ._job_runner import validate_platform
from .backends.internal import InternalSandboxBackend
from .contract import SandboxExecutionRequest, SandboxExecutionResult, SandboxSecretReference
from .job_backend import SandboxJobLaunch, SandboxJobObservation, SandboxJobOriginalDeadlineProof
from .jobs import SandboxJobBackendBinding, SandboxJobLease, SandboxJobProfile, SandboxJobStore


def internal_job_profile(config: SandboxConfig) -> SandboxJobProfile:
    return SandboxJobProfile(
        "internal",
        "embedded-sqlite-supervisor",
        "1",
        effect_request_digest(
            "owa/internal-job-policy/v1",
            {
                "sandbox": config.model_dump(mode="json"),
                "platform": sys.platform,
                "storage": "sqlite",
                "ipc": "private-hmac-files-v1",
                "output_cap": 262_144,
            },
        ),
        config.timeout_seconds,
    )


def launch_value(launch: SandboxJobLaunch) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(json.dumps(asdict(launch), allow_nan=False))
    return value


def parse_launch(value: dict[str, Any]) -> SandboxJobLaunch:
    if set(value) != {"lease", "profile", "request", "request_digest", "deadline_at", "submit_id"}:
        raise SandboxPolicyError("invalid sandbox supervisor launch envelope")
    request = dict(value["request"])
    request["arguments"] = tuple(request["arguments"])
    request["environment"] = tuple(
        (name, SandboxSecretReference(item["name"])) for name, item in request["environment"]
    )
    return SandboxJobLaunch(
        SandboxJobLease(**value["lease"]),
        SandboxJobProfile(**value["profile"]),
        SandboxExecutionRequest(**request),
        value["request_digest"],
        value["deadline_at"],
        value["submit_id"],
    )


def immutable(launch: SandboxJobLaunch) -> dict[str, Any]:
    value = launch_value(launch)
    value["lease"] = {
        "job_id": launch.lease.job_id,
        "admission_digest": launch.lease.admission_digest,
    }
    return value


class InternalSandboxJobBackend:
    def __init__(
        self,
        config: SandboxConfig,
        store: SandboxJobStore,
        *,
        profile: SandboxJobProfile | None = None,
    ) -> None:
        if store.connection.kind != "sqlite":
            raise SandboxPolicyError("internal independent jobs require embedded SQLite")
        self.config, self.store = config, store
        self._profile = internal_job_profile(config)
        if profile is not None and profile != self._profile:
            raise SandboxPolicyError("internal supervisor profile differs from deployment")
        validate_platform(InternalSandboxBackend(config))
        row = store.connection.execute("PRAGMA database_list").fetchone()
        if row is None or not row[2]:
            raise SandboxPolicyError("internal jobs require a persistent local SQLite file")
        self.database = Path(row[2]).absolute()
        if self.database.is_symlink() or not self.database.is_file():
            raise SandboxPolicyError("internal job database path is invalid")
        self.root = Path(config.workspace_root) / ".owa-job-supervisors"

    def profile(self) -> SandboxJobProfile:
        if internal_job_profile(self.config) != self._profile:
            raise SandboxPolicyError("internal sandbox job policy changed")
        validate_platform(InternalSandboxBackend(self.config))
        return self._profile

    def _verify(
        self,
        launch: SandboxJobLaunch,
        purpose: Literal["submit", "inspect", "control"],
        generation: int | None = None,
        reason: Literal["cancel", "deadline"] | None = None,
    ) -> None:
        from .job_validation import verify_backend_launch

        verify_backend_launch(
            self.store.connection,
            launch,
            expected_profile=self.profile(),
            purpose=purpose,
            control_generation=generation,
            reason=reason,
        )

    def _directory(self, launch: SandboxJobLaunch) -> Path:
        return self.root / launch.lease.job_id

    def _read(self, launch: SandboxJobLaunch) -> tuple[Path, bytes, dict[str, Any]]:
        directory = self._directory(launch)
        check_path(self.root, directory=True)
        key = read_key(directory / "auth.key")
        bootstrap = read_frame(directory / "bootstrap.json", key)
        if (
            not isinstance(bootstrap, dict)
            or immutable(parse_launch(bootstrap["launch"])) != immutable(launch)
            or bootstrap["profile"] != asdict(self.profile())
            or bootstrap["database"] != str(self.database)
        ):
            raise SandboxPolicyError("sandbox supervisor binding differs from original admission")
        return directory, key, bootstrap

    def _observation(
        self, launch: SandboxJobLaunch, value: dict[str, Any], bootstrap: dict[str, Any]
    ) -> SandboxJobObservation:
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "immutable",
                "instance_id",
                "state",
                "outcome",
                "reason",
                "control_ack_generation",
                "pid",
            }
            or value["immutable"] != immutable(launch)
            or value["instance_id"] != bootstrap["instance_id"]
        ):
            raise SandboxPolicyError("sandbox supervisor observation identity is invalid")
        binding = SandboxJobBackendBinding(
            launch.lease.job_id,
            launch.request_digest,
            "embedded-sqlite-supervisor",
            value["instance_id"],
        )
        return SandboxJobObservation(
            value["state"],
            binding,
            SandboxExecutionResult(**value["outcome"]) if value["outcome"] is not None else None,
            value["reason"],
            value["control_ack_generation"],
            SandboxJobOriginalDeadlineProof(
                launch.lease.admission_digest,
                launch.deadline_at,
                effect_request_digest("owa/internal-job-terminal-proof/v1", value),
            )
            if value["state"] == "terminal" and value["reason"] == "deadline"
            else None,
        )

    async def _read_observation(
        self,
        launch: SandboxJobLaunch,
        purpose: Literal["inspect", "control"],
        generation: int | None = None,
        reason: Literal["cancel", "deadline"] | None = None,
    ) -> tuple[Path, bytes, dict[str, Any], SandboxJobObservation]:
        # Windows observation reads can transiently report EACCES. At most three
        # complete verified reads, with 60ms total wait;
        # unreadable evidence never becomes a known outcome or a new submission.
        for delay in (0.02, 0.04, None):
            self._verify(launch, purpose, generation, reason)
            directory, key, bootstrap = self._read(launch)
            try:
                frame = read_frame(directory / "observation.json", key)
            except PermissionError as error:
                if os.name != "nt" or error.errno != errno.EACCES or delay is None:
                    raise
                await asyncio.sleep(delay)
                continue
            return directory, key, bootstrap, self._observation(launch, frame, bootstrap)
        raise SandboxPolicyError("sandbox supervisor observation is unavailable")

    async def submit(self, launch: SandboxJobLaunch) -> SandboxJobObservation:
        self._verify(launch, "submit")
        Path(self.config.workspace_root).mkdir(parents=True, exist_ok=True)
        private_directory(self.root)
        directory = self._directory(launch)
        if directory.exists():
            return await self.inspect(launch, None)
        private_directory(directory)
        try:
            key = create_key(directory / "auth.key")
        except FileExistsError:
            return await self.inspect(launch, None)
        bootstrap = {
            "launch": launch_value(launch),
            "profile": asdict(self.profile()),
            "config": self.config.model_dump(mode="json", exclude_unset=True),
            "database": str(self.database),
            "instance_id": "supervisor_" + uuid.uuid4().hex,
        }
        # Persist before starting any OS process. Any gap now is inspection-only.
        write_frame(directory / "bootstrap.json", bootstrap, key, exclusive=True)
        environment = {"HOME": str(directory), "TMPDIR": str(directory), "PYTHONUNBUFFERED": "1"}
        if os.name == "nt" and os.getenv("SYSTEMROOT"):
            environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        names = set(self.config.inherited_environment)
        names.update(
            value.name
            for _, value in launch.request.environment
            if isinstance(value, SandboxSecretReference)
        )
        for name in names:
            if name in os.environ:
                environment[name] = os.environ[name]
        self._verify(launch, "submit")
        # Common bundled supervisor only; no SDK launcher, arbitrary executable,
        # ambient working directory, package install or inherited descriptors.
        process = await asyncio.to_thread(
            subprocess.Popen,
            [
                sys.executable,
                "-I",
                "-m",
                "open_workflow_agent.sandbox.job_supervisor",
                str(directory),
            ],
            cwd=directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=os.name == "posix",
            creationflags=0x00000008 | 0x00000200 if os.name == "nt" else 0,
        )
        async with asyncio.timeout(5):
            while not (directory / "observation.json").exists():
                if process.poll() is not None:
                    return SandboxJobObservation("unknown")
                await asyncio.sleep(0.02)
        return await self.inspect(launch, None)

    async def inspect(
        self, launch: SandboxJobLaunch, binding: SandboxJobBackendBinding | None
    ) -> SandboxJobObservation:
        try:
            directory, key, bootstrap, value = await self._read_observation(launch, "inspect")
        except FileNotFoundError:
            return SandboxJobObservation("unknown")
        if binding is not None and value.binding != binding:
            raise SandboxPolicyError("sandbox supervisor instance changed")
        if value.state in {"accepted", "running"}:
            try:
                heartbeat = read_frame(directory / "heartbeat.json", key)
            except FileNotFoundError:
                return SandboxJobObservation("unknown", value.binding)
            if (
                set(heartbeat) != {"instance_id", "time"}
                or heartbeat["instance_id"] != bootstrap["instance_id"]
                or not 0 <= time.time() - heartbeat["time"] <= 2
            ):
                return SandboxJobObservation("unknown", value.binding)
        return value

    async def cancel(
        self,
        launch: SandboxJobLaunch,
        binding: SandboxJobBackendBinding,
        *,
        generation: int,
        reason: Literal["cancel", "deadline"],
    ) -> SandboxJobObservation:
        directory, key, bootstrap, current = await self._read_observation(
            launch, "control", generation, reason
        )
        if current.binding != binding:
            raise SandboxPolicyError("sandbox supervisor control instance changed")
        if current.state == "terminal":
            return current
        write_frame(
            directory / "control.json",
            {
                "launch": launch_value(launch),
                "instance_id": bootstrap["instance_id"],
                "generation": generation,
                "reason": reason,
            },
            key,
        )
        async with asyncio.timeout(5):
            while True:
                result = await self.inspect(launch, binding)
                if result.state in {"terminal", "unknown"}:
                    return result
                await asyncio.sleep(0.02)
