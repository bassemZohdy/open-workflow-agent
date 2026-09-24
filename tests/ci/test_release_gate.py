from __future__ import annotations

import pytest

from ci.release_gate import GateError, WorkflowRun, exact_head_state, wait_for_companions

SHA = "a" * 40
ANCESTOR = "b" * 40


def run(sha: str, status: str, conclusion: str | None = None) -> WorkflowRun:
    return {"headSha": sha, "status": status, "conclusion": conclusion}


def test_ancestor_success_does_not_cover_a_pending_exact_head() -> None:
    assert exact_head_state([run(ANCESTOR, "completed", "success")], SHA) == "missing"
    assert (
        exact_head_state([run(SHA, "in_progress"), run(ANCESTOR, "completed", "success")], SHA)
        == "pending"
    )


def test_gate_waits_for_delayed_exact_head_results() -> None:
    clock = [0.0]
    calls = {"external": 0, "postgres": 0}

    def get_runs(workflow: str) -> list[WorkflowRun]:
        calls[workflow] += 1
        if workflow == "external":
            if calls[workflow] == 1:
                return [run(ANCESTOR, "completed", "success")]
            return [run(SHA, "completed", "success")]
        if calls[workflow] == 1:
            return [run(SHA, "in_progress")]
        return [run(SHA, "completed", "success")]

    def advance(seconds: float) -> None:
        clock[0] += seconds

    wait_for_companions(
        SHA,
        ["external", "postgres"],
        get_runs,
        timeout_seconds=10,
        poll_seconds=1,
        monotonic=lambda: clock[0],
        sleep=advance,
    )
    assert calls == {"external": 2, "postgres": 2}


def test_gate_fails_closed_on_exact_head_failure() -> None:
    with pytest.raises(GateError, match="external failed"):
        wait_for_companions(
            SHA,
            ["external"],
            lambda _: [run(SHA, "completed", "failure")],
            timeout_seconds=10,
            poll_seconds=1,
        )


def test_gate_times_out_when_exact_head_never_appears() -> None:
    clock = [0.0]

    def advance(seconds: float) -> None:
        clock[0] += seconds

    with pytest.raises(GateError, match="Timed out.*missing"):
        wait_for_companions(
            SHA,
            ["external"],
            lambda _: [run(ANCESTOR, "completed", "success")],
            timeout_seconds=2,
            poll_seconds=1,
            monotonic=lambda: clock[0],
            sleep=advance,
        )
