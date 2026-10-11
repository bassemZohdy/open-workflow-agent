"""Optional PostgreSQL integration coverage for the common persistence stores."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from open_workflow_agent.approvals import APPROVAL_REQUEST_EVENT, ApprovalStore
from open_workflow_agent.effects import EffectIdentity, EffectJournal, effect_request_digest
from open_workflow_agent.errors import InvocationStateError, WorkflowExecutionError
from open_workflow_agent.events import EventEnvelope
from open_workflow_agent.forks import ForkDecision, ForkDecisionStore, ForkIdentity
from open_workflow_agent.knowledge import DeterministicEmbeddingProvider, KnowledgeService
from open_workflow_agent.memory import MemoryService
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.scheduling import ScheduleStore
from open_workflow_agent.storage import namespaced_datasource, resolve_datasource
from open_workflow_agent.workflow import compile_workflow


def _postgres_url() -> str:
    value = os.getenv("OWA_TEST_POSTGRES_URL")
    if not value:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    return value


def test_postgres_datasource_normalization_and_engine_namespace() -> None:
    url = _postgres_url()
    assert resolve_datasource(url) == url
    namespaced = namespaced_datasource(url, "owa_langgraph")
    assert "owa_langgraph%2Cpublic" in namespaced


@pytest.mark.parametrize("original", [None, False, 0, "", [], {"original": [1, 2]}])
def test_postgres_recovery_reads_input_without_creating_a_replacement(original):
    url = _postgres_url()
    pytest.importorskip("psycopg")
    first = InvocationStore(url)
    handle = first.create(
        engine="test",
        session_id=None,
        user_id=None,
        workflow_name="input-read",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
    )
    first.bind_input(handle, original)
    first.close()
    reopened = InvocationStore(url)
    try:
        assert reopened.read_input(handle) == original
        reopened.connection.execute(
            "DELETE FROM invocation_inputs WHERE invocation_id = ?", (handle.invocation_id,)
        )
        reopened.connection.commit()
        with pytest.raises(WorkflowExecutionError, match="invocation input could not be recovered"):
            reopened.read_input(handle)
        assert (
            reopened.connection.execute(
                "SELECT COUNT(*) FROM invocation_inputs WHERE invocation_id = ?",
                (handle.invocation_id,),
            ).fetchone()[0]
            == 0
        )
    finally:
        reopened.close()


@pytest.mark.parametrize("outcome", ["value", "null", "fault", "cancelled"])
def test_postgres_stale_write_generation_and_first_terminal_outcome(outcome):
    url = _postgres_url()
    pytest.importorskip("psycopg")
    first = InvocationStore(url)
    other = InvocationStore(url)
    stale = first.create(
        engine="test",
        session_id=None,
        user_id=None,
        workflow_name="write-guard",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
    )
    fresh = other.get(stale.invocation_id)
    try:
        other.update(fresh, status="waiting")
        other.update(fresh, status="running")
        with pytest.raises(InvocationStateError):
            first.update(stale, status="completed", output={"late": True})
        assert first.get(stale.invocation_id).status == "running"
        status = (
            "faulted"
            if outcome == "fault"
            else "cancelled"
            if outcome == "cancelled"
            else "completed"
        )
        output = {"retained": True} if outcome == "value" else None
        error = (
            {"code": status, "message": "retained", "details": {}}
            if status != "completed"
            else None
        )
        other.update(fresh, status=status, output=output, error=error)
        with pytest.raises(InvocationStateError):
            first.update(stale, status="faulted", error={"late": True})
        loaded = first.get(stale.invocation_id)
        first.update(loaded, status=status, output={"late": True}, error={"late": True})
        assert loaded.output == output and loaded.error == error
    finally:
        first.close()
        other.close()
    reopened = InvocationStore(url)
    try:
        saved = reopened.get(stale.invocation_id)
        assert saved.status == status and saved.output == output and saved.error == error
    finally:
        reopened.close()


def test_postgres_two_simultaneous_invocation_writers_retain_one_outcome():
    url = _postgres_url()
    pytest.importorskip("psycopg")
    stores = [InvocationStore(url) for _ in range(2)]
    initial = stores[0].create(
        engine="test",
        session_id=None,
        user_id=None,
        workflow_name="write-race",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
    )
    handles = [store.get(initial.invocation_id) for store in stores]
    barrier = Barrier(2)

    def write(index):
        barrier.wait(timeout=10)
        try:
            stores[index].update(handles[index], status="completed", output={"winner": index})
            return index
        except InvocationStateError:
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(write, range(2)))
        winners = [index for index in results if index is not None]
        assert len(winners) == 1
        saved = stores[0].get(initial.invocation_id)
        assert saved.status == "completed" and saved.output == {"winner": winners[0]}
    finally:
        for store in stores:
            store.close()


@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
def test_postgres_effect_intent_owner_and_first_outcome_survive_reopening(outcome) -> None:
    url = _postgres_url()
    pytest.importorskip("psycopg")
    identity = EffectIdentity(str(uuid4()), "fingerprint", "/do/0/action", (("action", 1),), "http")
    digest = effect_request_digest("http", {"body": {"operation": "test"}})
    first = EffectJournal(url)
    other = EffectJournal(url)
    try:
        pending = first.begin(identity, digest, "owner", operation_id="operation")
        assert other.begin(identity, digest, "other-owner") == pending
        with pytest.raises(WorkflowExecutionError, match="does not own"):
            other.finish(identity, "other-owner", output={"incorrect": True})
        receipt = first.finish(
            identity,
            "owner",
            output={"recorded": True} if outcome == "value" else None,
            error={"code": "model_error", "message": "recorded", "details": {}}
            if outcome == "fault"
            else None,
            error_status_code=500 if outcome == "fault" else None,
        )
        assert receipt.status == ("faulted" if outcome == "fault" else "completed")
        assert other.finish(identity, "owner", output={"late": True}) == receipt
    finally:
        first.close()
        other.close()
    reopened = EffectJournal(url)
    try:
        assert reopened.begin(identity, digest, "recovered-owner") == receipt
    finally:
        reopened.close()


@pytest.mark.parametrize("original", [None, False, {"raw": [1, 2]}])
def test_postgres_raw_input_and_terminal_result_survive_reopening(original) -> None:
    url = _postgres_url()
    pytest.importorskip("psycopg")
    first = InvocationStore(url)
    second = InvocationStore(url)
    handle = first.create(
        engine="test",
        session_id=None,
        user_id=None,
        workflow_name="input-recovery",
        workflow_version="1.0.0",
        workflow_fingerprint="fingerprint",
    )
    try:
        assert first.bind_input(handle, original) == original
        assert second.bind_input(handle, {"resume": True}) == original
        first.update(handle, status="completed", output={"original": original})
        saved = second.get(handle.invocation_id)
        assert saved is not None and saved.status == "completed"
        assert saved.output == {"original": original}
    finally:
        first.close()
        second.close()
    reopened = InvocationStore(url)
    try:
        saved = reopened.get(handle.invocation_id)
        assert saved is not None and saved.status == "completed"
        assert saved.output == {"original": original}
        assert reopened.bind_input(saved, {"new": True}) == original
    finally:
        reopened.close()


def test_postgres_fork_decision_claim_and_cleanup_survive_independent_connections() -> None:
    url = _postgres_url()
    pytest.importorskip("psycopg")
    identity = ForkIdentity(str(uuid4()), "fingerprint", "/do/0/race", (("/do/0/race", 1),))
    winner = ForkDecision(1, "completed", None, {}, None, "operation", (0,))
    later = replace(winner, winner=0, output={"other": True}, cancellation_intents=(1,))
    first = ForkDecisionStore(url)
    second = ForkDecisionStore(url)
    try:
        assert first.claim(identity, winner) == winner
        assert second.claim(identity, later) == winner
        second.mark_drained(identity)
        assert first.get(identity) == replace(winner, drained=True)
    finally:
        first.close()
        second.close()
    reopened = ForkDecisionStore(url)
    try:
        assert reopened.get(identity) == replace(winner, drained=True)
        assert reopened.get(replace(identity, occurrence=(("/do/0/race", 2),))) is None
    finally:
        reopened.close()


def test_postgres_common_stores_are_durable_and_isolated(tmp_path) -> None:
    url = _postgres_url()
    psycopg = pytest.importorskip("psycopg")
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    (knowledge_root / "policy.md").write_text("PostgreSQL persistence policy", encoding="utf-8")

    invocations = InvocationStore(url)
    schedules = ScheduleStore(url)
    approvals = ApprovalStore(url)
    memory = MemoryService(url)
    knowledge = KnowledgeService(
        knowledge_root,
        url,
        embedding=DeterministicEmbeddingProvider(8),
    )
    handle = invocations.create(
        engine="test",
        session_id="postgres-session",
        user_id="postgres-user",
        workflow_name="postgres",
        workflow_version="1.0.0",
        workflow_fingerprint="fingerprint",
    )
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "postgres",
                "name": "scheduled",
                "version": "1.0.0",
            },
            "schedule": {"after": {"seconds": 1}},
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )
    schedule = schedules.create(plan, {"durable": True}, operation_key="postgres-schedule")
    approvals.create_request(
        EventEnvelope(
            id="postgres-approval",
            source="urn:postgres-test",
            type=APPROVAL_REQUEST_EVENT,
            time="2026-08-25T00:00:00Z",
            subject="postgres-approval",
            data={"question": "persist?"},
        )
    )
    approvals.decide(
        "postgres-approval",
        decision="approved",
        operator_id="postgres-operator",
        operation_key="postgres-approval-decision",
    )
    memory_id = memory.add("durable PostgreSQL memory", {"source": "test"})
    assert knowledge.reload() == {
        "added": 1,
        "updated": 0,
        "deleted": 0,
        "unchanged": 0,
    }
    invocations.close()
    schedules.close()
    approvals.close()
    memory.close()
    knowledge.close()

    reopened_invocations = InvocationStore(url)
    reopened_schedules = ScheduleStore(url)
    reopened_approvals = ApprovalStore(url)
    reopened_memory = MemoryService(url)
    reopened_knowledge = KnowledgeService(
        knowledge_root,
        url,
        embedding=DeterministicEmbeddingProvider(8),
    )
    try:
        assert reopened_invocations.get(handle.invocation_id) == handle
        persisted_schedule = reopened_schedules.get(schedule.schedule_id)
        assert persisted_schedule is not None
        assert persisted_schedule.input_data == {"durable": True}
        persisted_approval = reopened_approvals.get("postgres-approval")
        assert persisted_approval is not None
        assert persisted_approval.status == "approved"
        assert persisted_approval.operator_id == "postgres-operator"
        assert reopened_memory.search("PostgreSQL", limit=5)[0]["id"] == memory_id
        assert reopened_knowledge.reload()["unchanged"] == 1
        assert reopened_knowledge.search("persistence", limit=1)[0]["path"].endswith("policy.md")

        with psycopg.connect(url) as connection:
            rows = connection.execute(
                """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name IN (
                    'owa_approvals', 'owa_runtime', 'owa_memory', 'owa_knowledge'
                )
                ORDER BY schema_name
                """
            ).fetchall()
        assert [row[0] for row in rows] == [
            "owa_approvals",
            "owa_knowledge",
            "owa_memory",
            "owa_runtime",
        ]
    finally:
        reopened_invocations.close()
        reopened_schedules.close()
        reopened_approvals.close()
        reopened_memory.close()
        reopened_knowledge.close()
