"""Bundled independent supervisor entry point; never an SDK process launcher."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from ..config import SandboxConfig
from ..errors import SandboxPolicyError
from ..storage import StorageConnection
from ._job_ipc import check_path, read_frame, read_key, write_frame
from ._job_runner import ControlMutation, run_job, validate_platform
from .backends.internal import InternalSandboxBackend
from .internal_jobs import immutable, internal_job_profile, parse_launch
from .job_backend import SandboxJobLaunch
from .job_validation import backend_mutation_fence, verify_backend_launch


async def supervise(directory: Path) -> None:
    if not directory.is_absolute() or directory.name[:4] != "job_":
        raise SandboxPolicyError("invalid internal supervisor directory")
    check_path(directory, directory=True)
    key = read_key(directory / "auth.key")
    bootstrap = read_frame(directory / "bootstrap.json", key)
    if not isinstance(bootstrap, dict) or set(bootstrap) != {
        "launch",
        "profile",
        "config",
        "database",
        "instance_id",
    }:
        raise SandboxPolicyError("invalid internal supervisor bootstrap")
    launch = parse_launch(bootstrap["launch"])
    config = SandboxConfig.model_validate(bootstrap["config"])
    expected = internal_job_profile(config)
    backend = InternalSandboxBackend(config)
    validate_platform(backend)
    if (
        bootstrap["profile"] != asdict(expected)
        or directory.name != launch.lease.job_id
        or directory.parent != Path(config.workspace_root) / ".owa-job-supervisors"
    ):
        raise SandboxPolicyError("supervisor deployment binding differs")
    database = Path(bootstrap["database"])
    if not database.is_absolute() or database.is_symlink() or not database.is_file():
        raise SandboxPolicyError("supervisor SQLite binding is invalid")
    connection = StorageConnection(
        sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=3), "sqlite"
    )
    connection.execute("PRAGMA query_only=ON")
    state: dict[str, Any] = {
        "immutable": immutable(launch),
        "instance_id": bootstrap["instance_id"],
        "state": "unknown",
        "outcome": None,
        "reason": "exited",
        "control_ack_generation": None,
        "pid": os.getpid(),
    }
    stop = asyncio.Event()
    background: list[asyncio.Task[Any]] = []
    pending_control: tuple[SandboxJobLaunch, int, Literal["cancel", "deadline"]] | None = None

    def publish() -> None:
        write_frame(directory / "observation.json", state, key)

    def started(pid: int) -> None:
        state["state"] = "running"
        write_frame(
            directory / "heartbeat.json",
            {"instance_id": bootstrap["instance_id"], "time": time.time()},
            key,
        )
        publish()

    async def heartbeat() -> None:
        while True:
            write_frame(
                directory / "heartbeat.json",
                {"instance_id": bootstrap["instance_id"], "time": time.time()},
                key,
            )
            await asyncio.sleep(0.25)

    def expire() -> None:
        state["reason"], state["control_ack_generation"] = "deadline", None
        stop.set()

    def control_mutation() -> ControlMutation | None:
        nonlocal pending_control
        if pending_control is None:
            return None
        selected, generation, reason = pending_control
        pending_control = None

        def applied() -> None:
            state["reason"], state["control_ack_generation"] = reason, generation

        return (
            backend_mutation_fence(
                database,
                selected,
                expected_profile=expected,
                purpose="control",
                control_generation=generation,
                reason=reason,
            ),
            applied,
        )

    async def deadline(running: asyncio.Task[Any]) -> None:
        await asyncio.sleep(max(0, launch.deadline_at - time.time()))
        if not running.done():
            expire()

    async def controls(running: asyncio.Task[Any]) -> None:
        nonlocal pending_control
        while not running.done():
            try:
                value = read_frame(directory / "control.json", key)
            except (FileNotFoundError, SandboxPolicyError, ValueError, TypeError):
                await asyncio.sleep(0.05)
                continue
            try:
                if not isinstance(value, dict) or set(value) != {
                    "launch",
                    "instance_id",
                    "generation",
                    "reason",
                }:
                    raise SandboxPolicyError("invalid supervisor control envelope")
                selected = parse_launch(value["launch"])
                if (
                    immutable(selected) != immutable(launch)
                    or value["instance_id"] != bootstrap["instance_id"]
                    or value["reason"] not in {"cancel", "deadline"}
                ):
                    raise SandboxPolicyError("supervisor control differs from accepted job")
                reason: Literal["cancel", "deadline"] = value["reason"]
                generation = value["generation"]
                if (
                    state["control_ack_generation"] != generation
                    and not running.done()
                    and not stop.is_set()
                ):
                    verify_backend_launch(
                        connection,
                        selected,
                        expected_profile=expected,
                        purpose="control",
                        control_generation=generation,
                        reason=reason,
                    )
                    # This is a request, not an acknowledgement. The runner
                    # revalidates under the RW fence at physical termination.
                    pending_control = selected, generation, reason
                    stop.set()
            except Exception:
                # A rejected stale/tampered control cannot disable subsequent
                # valid controls or change the original lifetime budget.
                pass
            await asyncio.sleep(0.05)

    try:
        verify_backend_launch(connection, launch, expected_profile=expected, purpose="submit")
        remaining = launch.deadline_at - time.time()
        if not 0 < remaining <= expected.timeout_seconds:
            raise SandboxPolicyError("sandbox job original absolute deadline expired")
        # This one-time supervisor never reconstructs/retries a spawn after a
        # crash. Its immutable bootstrap remains inspection-only forever.
        running = asyncio.create_task(
            run_job(
                backend,
                launch.request,
                remaining=remaining,
                started=started,
                stop=stop,
                deadline_reached=expire,
                deadline_due=lambda: time.time() >= launch.deadline_at,
                launch_fence=lambda: backend_mutation_fence(
                    database, launch, expected_profile=expected, purpose="submit"
                ),
                control_mutation=control_mutation,
            )
        )
        background = [
            asyncio.create_task(heartbeat()),
            asyncio.create_task(deadline(running)),
            asyncio.create_task(controls(running)),
        ]
        try:
            result, terminated = await running
            if not terminated:
                state["reason"], state["control_ack_generation"] = "exited", None
            state["state"], state["outcome"] = "terminal", asdict(result)
            publish()
        except BaseException:
            state["state"], state["outcome"], state["control_ack_generation"] = (
                "unknown",
                None,
                None,
            )
            publish()
            raise
    finally:
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        connection.close()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(2)
    try:
        asyncio.run(supervise(Path(sys.argv[1])))
    except BaseException:
        # IPC contains only authenticated categories/outcomes; inherited stdio
        # is disabled and no exception/secret message is emitted by this service.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
