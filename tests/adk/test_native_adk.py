from __future__ import annotations

import asyncio

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_adk.native import ADK_AVAILABLE


@pytest.mark.asyncio
async def test_native_benchmark_closes_resources_before_temporary_storage_cleanup():
    if not ADK_AVAILABLE:
        pytest.skip("google-adk optional extra is not installed")
    from benchmarks.engines import run_engine_benchmark

    result = await run_engine_benchmark("adk", iterations=1, concurrency=1)
    assert result["interruption_resume"]["status"] == "completed"
    assert result["native_checkpoint_bytes_delta"] > 0


def test_native_migration_probe_closes_database_and_preserves_old_schema_rejection(tmp_path):
    if not ADK_AVAILABLE:
        pytest.skip("google-adk optional extra is not installed")
    import sqlite3
    from contextlib import closing

    from open_workflow_agent_adk.native import _ClosingSqliteSessionService

    path = tmp_path / "old.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE events (id TEXT)")
        connection.commit()
    with pytest.raises(RuntimeError, match="old schema"):
        _ClosingSqliteSessionService(str(path))
    # Windows refuses this rename if the migration probe retains its handle.
    path.rename(tmp_path / "migratable.sqlite3")


@pytest.mark.asyncio
async def test_native_adk_dynamic_plan_invocation(tmp_path):
    if not ADK_AVAILABLE:
        pytest.skip("google-adk optional extra is not installed")
    services = RuntimeServices(
        RuntimeConfig(), model=FakeModel({"response": "native"}), database_root=tmp_path
    )
    engine = AdkWorkflowEngine()
    await engine.initialize(services)
    plan = compile_workflow()
    handle = services.invocations.create(
        engine="adk",
        session_id="native-session",
        user_id="native-user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    result = await engine.invoke(plan, handle, {"question": "hello"})
    assert result.status == "completed"
    assert result.output == {"response": "native"}
    services.close()

    restarted_services = RuntimeServices(
        RuntimeConfig(), model=FakeModel({"response": "resumed"}), database_root=tmp_path
    )
    restarted_engine = AdkWorkflowEngine()
    await restarted_engine.initialize(restarted_services)
    persisted = restarted_services.invocations.get(handle.invocation_id)
    assert persisted is not None
    resumed = await restarted_engine.resume(persisted, {"question": "again"}, plan)
    assert resumed.status == "completed"
    restarted_services.close()


@pytest.mark.asyncio
async def test_native_adk_interrupted_resume_reuses_completed_action_receipt(tmp_path):
    if not ADK_AVAILABLE:
        pytest.skip("google-adk optional extra is not installed")
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "tests",
            "name": "interrupted",
            "version": "1.0.0",
        },
        "do": [
            {
                "side_effect": {
                    "call": "http",
                    "with": {
                        "method": "POST",
                        "endpoint": "https://service.test/side-effect",
                        "body": {"action": "once-or-more"},
                    },
                }
            },
            {"pause": {"wait": {"seconds": 300}}},
            {"finish": {"set": {"done": True}}},
        ],
    }
    calls: list[str] = []
    reached_side_effect = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Idempotency-Key"])
        reached_side_effect.set()
        return httpx.Response(200, json={"ok": True})

    config = RuntimeConfig()
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    services.tools.protocols = services.protocols
    engine = AdkWorkflowEngine()
    await engine.initialize(services)
    plan = compile_workflow(workflow)
    handle = services.invocations.create(
        engine="adk",
        session_id="interrupted-session",
        user_id="interrupted-user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    await asyncio.wait_for(reached_side_effect.wait(), timeout=5)
    async with asyncio.timeout(5):
        while handle.status != "waiting":
            await asyncio.sleep(0.005)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    services.close()

    restarted = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    restarted.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    restarted.tools.protocols = restarted.protocols
    resumed_engine = AdkWorkflowEngine()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None
    assert persisted.status == "waiting"
    resumed = await resumed_engine.resume(persisted, {}, plan)
    assert resumed.status == "completed"
    assert resumed.output == {"done": True}
    assert len(calls) == 1
    restarted.close()
