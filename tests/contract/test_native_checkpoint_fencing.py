"""Actual SDK writers cannot advance checkpoints after execution ownership changes."""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.lifecycle import TERMINAL_INVOCATION_STATES
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import open_storage
from open_workflow_agent.workflow import compile_workflow


def _snapshot(
    services: RuntimeServices, handle: ExecutionHandle, backend: str
) -> dict[str, list[Any]]:
    native = open_storage(services.engine_database_path(handle.engine), f"owa_{handle.engine}")
    try:
        if handle.engine == "adk":
            return {
                table: sorted(
                    native.execute(
                        f"SELECT * FROM {table} WHERE {column} = ?", (handle.session_id,)
                    ).fetchall(),
                    key=repr,
                )
                for table, column in (("sessions", "id"), ("events", "session_id"))
            }
        tables = (
            ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
            if backend == "postgresql"
            else ("checkpoints", "writes")
        )
        return {
            table: sorted(native.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
            for table in tables
        }
    finally:
        native.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("takeover", [False, True])
@pytest.mark.parametrize("boundary", ["initial", "after_action"])
async def test_native_sdk_mutation_rejects_expiry_and_takeover_without_advancing_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: type[Any],
    backend: str,
    takeover: bool,
    boundary: str,
) -> None:
    engine = engine_type()
    native_available = (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    )
    if not native_available:
        pytest.skip("this engine's native SDK is not installed")
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    request_entered, request_finish = asyncio.Event(), asyncio.Event()
    writer_armed, writer_entered, writer_finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requests: list[bytes] = []

    async def request(message: httpx.Request) -> httpx.Response:
        requests.append(message.content)
        request_entered.set()
        await request_finish.wait()
        return httpx.Response(200, json={"accepted": True})

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(request)))
    services.tools.protocols = services.protocols

    def gated(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def write(self: Any, *args: Any, **kwargs: Any) -> Any:
            if writer_armed.is_set():
                writer_entered.set()
                await writer_finish.wait()
            return await original(self, *args, **kwargs)

        return write

    if engine_name == "adk":
        module = importlib.import_module("open_workflow_agent_adk.native")
        sdk = getattr(
            module,
            "_FencedDatabaseSessionService" if datasource else "_ClosingSqliteSessionService",
        )
        methods: tuple[str, ...] = ("append_event",)
    else:
        module = importlib.import_module("open_workflow_agent_langgraph.native")
        sdk = getattr(
            module, "_FencedAsyncPostgresSaver" if datasource else "_FencedAsyncSqliteSaver"
        )
        methods = ("aput", "aput_writes")
    for method in methods:
        monkeypatch.setattr(sdk, method, gated(getattr(sdk, method)))
    # Isolate native transaction fencing from the separately tested heartbeat.
    engine._execution_ownership_seconds = 600
    running = None
    try:
        await engine.initialize(services)
        plan = compile_workflow(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "test",
                    "name": "native-fence",
                    "version": "1.0.0",
                },
                "do": [
                    {
                        "action": {
                            "call": "http",
                            "with": {
                                "method": "POST",
                                "endpoint": "https://service.test/action",
                                "body": "${ . }",
                            },
                        }
                    }
                ],
            }
        )
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        if boundary == "initial":
            writer_armed.set()
        running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
        if boundary == "after_action":
            await asyncio.wait_for(request_entered.wait(), 20)
            writer_armed.set()
            request_finish.set()
        await asyncio.wait_for(writer_entered.wait(), 20)
        common_before = handle._persisted_payload
        native_before = _snapshot(services, handle, backend)
        if boundary == "after_action":
            assert any(native_before.values())
        completed_before = services.invocations.get(handle.invocation_id)
        assert completed_before is not None
        lease = engine._execution_leases[handle.invocation_id]
        services.execution_ownership.connection.execute(
            "UPDATE invocation_ownership SET expires_at = 1 WHERE invocation_id = ?",
            (handle.invocation_id,),
        )
        services.execution_ownership.connection.commit()
        successor = services.execution_ownership.acquire(handle) if takeover else None
        writer_finish.set()
        if completed_before.status in TERMINAL_INVOCATION_STATES:
            result = await asyncio.wait_for(running, 20)
            assert result.status == completed_before.status
            assert (
                result.output == completed_before.output and result.error == completed_before.error
            )
        else:
            with pytest.raises(InvocationStateConflict):
                await asyncio.wait_for(running, 20)
        current = services.invocations.get(handle.invocation_id)
        assert current is not None and current._persisted_payload == common_before
        assert _snapshot(services, handle, backend) == native_before
        assert len(requests) == (1 if boundary == "after_action" else 0)
        assert not engine._active and not engine._execution_leases and not engine._ownership_tasks
        if successor is not None:
            assert successor.generation == lease.generation + 1
            services.execution_ownership.verify(successor)
            services.execution_ownership.release(successor)
    finally:
        writer_finish.set()
        request_finish.set()
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()
