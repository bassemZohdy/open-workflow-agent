from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.forks import ForkDecision, ForkDecisionStore, ForkIdentity


def _identity():
    return ForkIdentity("invocation", "fingerprint", "/do/0/race", (("/do/0/race", 1),))


def _decision(winner=1, output=None, status="completed", error=None):
    return ForkDecision(winner, status, output, {}, error, "operation", (1 - winner,))


def test_first_winner_and_cancellation_intents_survive_reopening_storage(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    first = ForkDecisionStore(path)
    other = ForkDecisionStore(path)
    winner = _decision(output={"shared": "second"})
    try:
        assert first.get(_identity()) is None
        assert first.claim(_identity(), winner) == winner
        assert other.claim(_identity(), _decision(0, {"shared": "first"})) == winner
        assert other.get(_identity()).cancellation_intents == (0,)
        other.mark_drained(_identity())
        assert first.claim(_identity(), _decision(0)) == replace(winner, drained=True)
    finally:
        first.close()
        other.close()
    reopened = ForkDecisionStore(path)
    try:
        assert reopened.get(_identity()) == replace(winner, drained=True)
    finally:
        reopened.close()


def test_simultaneous_independent_connections_agree_on_one_committed_winner(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    initial = ForkDecisionStore(path)
    initial.close()
    ready = Barrier(2)

    def contend(index):
        store = ForkDecisionStore(path)
        try:
            ready.wait(timeout=10)
            return store.claim(_identity(), _decision(index, {"branch": index}))
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as threads:
        results = list(threads.map(contend, (0, 1)))
    assert results[0] == results[1]
    assert results[0].winner in {0, 1}
    assert results[0].output == {"branch": results[0].winner}
    assert results[0].cancellation_intents == (1 - results[0].winner,)


@pytest.mark.parametrize(
    "changes",
    [
        {"invocation_id": "another"},
        {"workflow_fingerprint": "another"},
        {"task_reference": "/do/1/race"},
        {"occurrence": (("/do/0/race", 2),)},
        {"occurrence": (("/do/0/loop", 2), ("/do/0/race", 1))},
    ],
)
def test_distinct_invocations_versions_call_sites_and_visits_do_not_share_a_winner(changes):
    store = ForkDecisionStore()
    try:
        store.claim(_identity(), _decision())
        identity = replace(_identity(), **changes)
        assert store.get(identity) is None
        assert store.claim(identity, _decision(0)).winner == 0
        assert store.get(_identity()).winner == 1
    finally:
        store.close()


@pytest.mark.parametrize(
    "decision",
    [
        _decision(output=None),
        _decision(status="faulted", error={"code": "workflow_execution_error", "details": {}}),
        _decision(status="ended", output=[False, 3, "value"]),
    ],
)
def test_null_fault_and_end_decisions_keep_their_distinct_json_meaning(decision):
    store = ForkDecisionStore()
    try:
        assert store.claim(_identity(), decision) == decision
    finally:
        store.close()


@pytest.mark.parametrize("output", [float("nan"), float("inf"), object(), {1: "invalid-key"}])
def test_invalid_output_cannot_create_or_replace_a_winner(output):
    store = ForkDecisionStore()
    try:
        with pytest.raises(WorkflowExecutionError, match="could not be stored"):
            store.claim(_identity(), _decision(output=output))
        assert store.get(_identity()) is None
        original = store.claim(_identity(), _decision(output="committed"))
        with pytest.raises(WorkflowExecutionError, match="could not be stored"):
            store.claim(_identity(), _decision(0, output))
        assert store.get(_identity()) == original
    finally:
        store.close()


def test_absent_cleanup_corrupt_storage_and_closed_storage_fail_with_common_errors():
    store = ForkDecisionStore()
    with pytest.raises(WorkflowExecutionError, match="cleanup could not be recorded"):
        store.mark_drained(_identity())
    store.claim(_identity(), _decision())
    store.connection.execute(
        "UPDATE competitive_fork_decisions SET payload = ? WHERE identity = ?",
        ('{"invalid":true}', _identity().key),
    )
    store.connection.commit()
    with pytest.raises(WorkflowExecutionError, match="decision could not be read"):
        store.get(_identity())
    store.close()
    with pytest.raises(WorkflowExecutionError, match="decision could not be stored"):
        store.claim(_identity(), _decision())


def test_runtime_composes_durable_decisions_with_common_storage_and_closes_them(tmp_path):
    from open_workflow_agent.catalog import FakeModel
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    decision = services.fork_decisions.claim(_identity(), _decision(output="winner"))
    services.close()
    with pytest.raises(WorkflowExecutionError, match="decision could not be read"):
        services.fork_decisions.get(_identity())
    reopened = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        assert reopened.fork_decisions.get(_identity()) == decision
    finally:
        reopened.close()


def test_corrupt_nonfinite_stored_output_is_rejected_before_replay():
    import json

    store = ForkDecisionStore()
    try:
        store.claim(_identity(), _decision())
        row = store.connection.execute(
            "SELECT payload FROM competitive_fork_decisions WHERE identity = ?",
            (_identity().key,),
        ).fetchone()
        payload = json.loads(row[0])
        payload["output"] = float("nan")
        store.connection.execute(
            "UPDATE competitive_fork_decisions SET payload = ? WHERE identity = ?",
            (json.dumps(payload), _identity().key),
        )
        store.connection.commit()
        with pytest.raises(WorkflowExecutionError, match="decision could not be read"):
            store.get(_identity())
    finally:
        store.close()
