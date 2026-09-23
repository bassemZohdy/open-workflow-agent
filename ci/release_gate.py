"""Wait for exact-commit companion acceptance before publishing images."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import TypedDict


class WorkflowRun(TypedDict):
    headSha: str
    status: str
    conclusion: str | None


class GateError(Exception):
    """A release precondition was not met."""


def fetch_runs(repository: str, workflow: str) -> list[WorkflowRun]:
    result = subprocess.run(
        [
            "gh",
            "run",
            "list",
            "--repo",
            repository,
            "--workflow",
            workflow,
            "--branch",
            "main",
            "--limit",
            "100",
            "--json",
            "headSha,status,conclusion",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise GateError(f"Could not read {workflow} runs from GitHub")
    try:
        runs: object = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GateError(f"Invalid {workflow} run response") from exc
    if not isinstance(runs, list) or any(
        not isinstance(run, dict)
        or not isinstance(run.get("headSha"), str)
        or not isinstance(run.get("status"), str)
        for run in runs
    ):
        raise GateError(f"Malformed {workflow} run response")
    return runs


def exact_head_state(runs: Sequence[WorkflowRun], release_sha: str) -> str:
    """Return the newest exact-head run state, never an ancestor's result."""
    for run in runs:
        if run["headSha"] != release_sha:
            continue
        if run["status"] != "completed":
            return "pending"
        return "success" if run["conclusion"] == "success" else "failure"
    return "missing"


def wait_for_companions(
    release_sha: str,
    workflows: Sequence[str],
    get_runs: Callable[[str], list[WorkflowRun]],
    *,
    timeout_seconds: float,
    poll_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Gate timeout and poll interval must be positive")
    deadline = monotonic() + timeout_seconds
    while True:
        outstanding: list[str] = []
        for workflow in workflows:
            state = exact_head_state(get_runs(workflow), release_sha)
            if state == "failure":
                raise GateError(f"{workflow} failed for {release_sha}")
            if state != "success":
                outstanding.append(f"{workflow} ({state})")
        if not outstanding:
            return
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise GateError(
                f"Timed out waiting for exact-head acceptance at {release_sha}: "
                + ", ".join(outstanding)
            )
        print("Waiting for " + ", ".join(outstanding), flush=True)
        sleep(min(poll_seconds, remaining))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--poll-seconds", type=float, default=15)
    parser.add_argument("workflows", nargs="+")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        parser.error("--sha must be a full Git commit SHA")
    try:
        wait_for_companions(
            args.sha,
            args.workflows,
            lambda workflow: fetch_runs(args.repository, workflow),
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
    except GateError as exc:
        print(f"Release gate failed: {exc}", file=sys.stderr)
        return 1
    print(f"Exact-head companion acceptance passed for {args.sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
