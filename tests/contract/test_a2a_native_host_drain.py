"""Cross-worker A2A projection after native-engine host drain."""

from __future__ import annotations

import asyncio
import importlib
from contextlib import AsyncExitStack
from typing import Any

import httpx
import pytest
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.services import RuntimeServices

HEADERS = {"a2a-version": A2A_PROTOCOL_VERSION}


def _native_engine(name: str) -> Any:
    if name == "adk":
        package = pytest.importorskip("open_workflow_agent_adk")
        native = importlib.import_module("open_workflow_agent_adk.native")
        if not native.ADK_AVAILABLE:
            pytest.skip("the independent native ADK environment is required")
        return package.AdkWorkflowEngine()
    package = pytest.importorskip("open_workflow_agent_langgraph")
    native = importlib.import_module("open_workflow_agent_langgraph.native")
    if not native.LANGGRAPH_AVAILABLE:
        pytest.skip("the independent native LangGraph environment is required")
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    return package.LangGraphWorkflowEngine()


async def _get_task(client: httpx.AsyncClient, task_id: str, transport: str) -> dict[str, Any]:
    if transport == "http_json":
        response = await client.get(f"/a2a/tasks/{task_id}", headers=HEADERS)
        assert response.status_code == 200, response.text
        return response.json()
    response = await client.post(
        "/a2a",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "GetTask",
            "params": {"id": task_id},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def _working_task(client: httpx.AsyncClient, task_id: str, transport: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for _ in range(60):
        result = await _get_task(client, task_id, transport)
        if result["status"]["state"] == "TASK_STATE_WORKING":
            return result
        await asyncio.sleep(0.05)
    raise AssertionError(f"task did not remain working after host drain: {result}")


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name", ["adk", "langgraph"])
@pytest.mark.parametrize("transport", ["jsonrpc", "http_json"])
async def test_native_host_drain_late_provider_result_is_read_only_cross_worker(
    tmp_path, engine_name: str, transport: str
) -> None:
    entered = asyncio.Event()
    cancellation_suppressed = asyncio.Event()
    late_provider_finished = asyncio.Event()
    release = asyncio.Event()

    async def slow_model(_prompt: Any) -> dict[str, str]:
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancellation_suppressed.set()
            await asyncio.shield(release.wait())
        late_provider_finished.set()
        return {"response": "late-provider-result"}

    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "workflow": {
                "catalog": [
                    {
                        "document": {
                            "dsl": "1.0.3",
                            "namespace": "a2a-native-host-drain",
                            "name": "slow",
                            "version": "1.0.0",
                        },
                        "do": [
                            {
                                "wait": {
                                    "call": "llm:1.0.0@default",
                                    "with": {"prompt": "wait for native host drain"},
                                }
                            }
                        ],
                    }
                ]
            },
            "a2a": {
                "enabled": True,
                "transport": transport,
                "skills": [{"id": "slow", "workflow": "slow"}],
            },
        }
    )
    source_engine = _native_engine(engine_name)
    source_services = RuntimeServices(config, model=FakeModel(slow_model), database_root=tmp_path)
    source_app = create_app(config=config, services=source_services, engine=source_engine)
    reader_services: RuntimeServices | None = None
    reader_stack = AsyncExitStack()
    try:
        async with AsyncExitStack() as source_stack:
            await source_stack.enter_async_context(source_app.router.lifespan_context(source_app))
            source_stack.callback(release.set)
            source_transport = httpx.ASGITransport(app=source_app)
            async with httpx.AsyncClient(
                transport=source_transport, base_url="http://test"
            ) as source_client:
                message = {
                    "messageId": "native-host-drain-message",
                    "role": "ROLE_USER",
                    "parts": [{"text": "start native task"}],
                    "metadata": {"skillId": "slow"},
                }
                if transport == "http_json":
                    started = await source_client.post(
                        "/a2a/message:send",
                        headers=HEADERS,
                        json={
                            "message": message,
                            "configuration": {"returnImmediately": True},
                        },
                    )
                    assert started.status_code == 200, started.text
                    task_id = started.json()["task"]["id"]
                else:
                    started = await source_client.post(
                        "/a2a",
                        headers=HEADERS,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "SendMessage",
                            "params": {
                                "message": message,
                                "configuration": {"returnImmediately": True},
                            },
                        },
                    )
                    assert started.status_code == 200, started.text
                    task_id = started.json()["result"]["task"]["id"]

                await asyncio.wait_for(entered.wait(), timeout=30)
                source_handle = source_services.invocations.get(task_id)
                assert source_handle is not None and source_handle.status == "running"
                persisted_before_drain = source_handle._persisted_payload

                reader_services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
                reader_app = create_app(
                    config=config,
                    services=reader_services,
                    engine=PortableWorkflowEngine(),
                )
                await reader_stack.enter_async_context(
                    reader_app.router.lifespan_context(reader_app)
                )

                async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
                    raise AssertionError("cross-worker Task reads must not replay workflow work")

                reader_app.state.engine.invoke = forbidden
                reader_app.state.engine.resume = forbidden
                reader_client = await reader_stack.enter_async_context(
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=reader_app), base_url="http://test"
                    )
                )

                def effect_rows(services: RuntimeServices) -> list[tuple[Any, ...]]:
                    rows = services.effect_journal.connection.execute(
                        "SELECT identity, owner, status, payload FROM workflow_effects "
                        "ORDER BY identity"
                    ).fetchall()
                    return [tuple(row) for row in rows]

                effects_before_drain = effect_rows(source_services)
                shutdown = asyncio.create_task(source_engine.shutdown())
                await asyncio.wait_for(cancellation_suppressed.wait(), timeout=10)
                assert not shutdown.done()

                before_result_first = await _working_task(reader_client, task_id, transport)
                before_result_second = await _working_task(reader_client, task_id, transport)
                assert before_result_second == before_result_first
                assert source_services.invocations.get(task_id)._persisted_payload == (
                    persisted_before_drain
                )
                assert reader_services.invocations.get(task_id)._persisted_payload == (
                    persisted_before_drain
                )
                assert effect_rows(source_services) == effects_before_drain
                assert effect_rows(reader_services) == effects_before_drain
                assert reader_services.model.calls == []
                assert len(source_services.model.calls) == 1

                release.set()
                await asyncio.wait_for(shutdown, timeout=30)
                assert late_provider_finished.is_set()
                source_after_result = source_services.invocations.get(task_id)
                assert source_after_result is not None and source_after_result.status == "running"
                persisted_after_result = source_after_result._persisted_payload
                effects_after_result = effect_rows(source_services)
                after_result_first = await _working_task(reader_client, task_id, transport)
                after_result_second = await _working_task(reader_client, task_id, transport)
                assert after_result_second == after_result_first == before_result_first
                reader_after_result = reader_services.invocations.get(task_id)
                assert reader_after_result is not None and reader_after_result.status == "running"
                assert reader_after_result._persisted_payload == persisted_after_result
                assert source_after_result._persisted_payload == persisted_before_drain
                assert effect_rows(reader_services) == effects_after_result == effects_before_drain
                assert reader_services.model.calls == []
                assert len(source_services.model.calls) == 1
    finally:
        release.set()
        await reader_stack.aclose()
        if reader_services is not None:
            reader_services.close()
        source_services.close()
