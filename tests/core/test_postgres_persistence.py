"""Optional PostgreSQL integration coverage for the common persistence stores."""

from __future__ import annotations

import os

import pytest
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


def test_postgres_common_stores_are_durable_and_isolated(tmp_path) -> None:
    url = _postgres_url()
    psycopg = pytest.importorskip("psycopg")
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    (knowledge_root / "policy.md").write_text("PostgreSQL persistence policy", encoding="utf-8")

    invocations = InvocationStore(url)
    schedules = ScheduleStore(url)
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
    memory_id = memory.add("durable PostgreSQL memory", {"source": "test"})
    assert knowledge.reload() == {
        "added": 1,
        "updated": 0,
        "deleted": 0,
        "unchanged": 0,
    }
    invocations.close()
    schedules.close()
    memory.close()
    knowledge.close()

    reopened_invocations = InvocationStore(url)
    reopened_schedules = ScheduleStore(url)
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
        assert reopened_memory.search("PostgreSQL", limit=5)[0]["id"] == memory_id
        assert reopened_knowledge.reload()["unchanged"] == 1
        assert reopened_knowledge.search("persistence", limit=1)[0]["path"].endswith("policy.md")

        with psycopg.connect(url) as connection:
            rows = connection.execute(
                """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name IN ('owa_runtime', 'owa_memory', 'owa_knowledge')
                ORDER BY schema_name
                """
            ).fetchall()
        assert [row[0] for row in rows] == ["owa_knowledge", "owa_memory", "owa_runtime"]
    finally:
        reopened_invocations.close()
        reopened_schedules.close()
        reopened_memory.close()
        reopened_knowledge.close()
