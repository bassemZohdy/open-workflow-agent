"""Executable operations retain known outcomes across native task interruption."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig, SandboxConfig
from open_workflow_agent.errors import OwaError, SandboxTimeoutError
from open_workflow_agent.sandbox import (
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxManager,
    compile_sandbox_workflow,
)
from open_workflow_agent.services import RuntimeServices
from test_native_checkpoint_fencing import _snapshot as native_snapshot

_IMAGE = "registry.example/worker@sha256:" + "a" * 64


class _CommittedSandbox:
    """Deterministic common-contract fake; no process/container is created."""

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.requests: list[SandboxExecutionRequest] = []
        self.committed = asyncio.Event()

    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        self.requests.append(request)
        self.committed.set()
        if self.outcome == "pending":
            await asyncio.Event().wait()
        if self.outcome == "fault":
            raise SandboxTimeoutError(
                "known sandbox timeout", details={"execution_id": request.execution_id}
            )
        return SandboxExecutionResult(request.execution_id, 0, "known output", "", 0.01)

    async def cancel(self, execution_id: str) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def capabilities(self) -> dict[str, Any]:
        return {"enabled": True, "backend": "test", "hardIsolation": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("kind", ["script", "shell", "container"])
@pytest.mark.parametrize("outcome", ["value", "fault", "pending"])
@pytest.mark.parametrize("changed_request", [False, True])
async def test_sandbox_outcome_or_uncertain_intent_prevents_replay_dispatch(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    kind: str,
    outcome: str,
    changed_request: bool,
    return_mode: str | None = None,
    require_native: bool = False,
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    sandbox = SandboxConfig.model_validate(
        {
            "enabled": True,
            "allow_shell": True,
            "backend": "docker" if kind == "container" else "internal",
            "docker": {"allowed_images": [_IMAGE]},
        }
    )
    config = (
        RuntimeConfig.model_validate(
            {"persistence": {"datasource": datasource}, "sandbox": sandbox.model_dump()}
        )
        if datasource
        else RuntimeConfig(sandbox=sandbox)
    )
    executor = _CommittedSandbox(outcome)

    def services() -> RuntimeServices:
        result = RuntimeServices(
            config, model=FakeModel(), database_root=None if datasource else tmp_path
        )
        result.sandbox = SandboxManager(executor)
        return result

    operation: dict[str, Any] = {"arguments": ["${ .request }"]}
    if kind == "script":
        operation.update(language="python", code="print('unused')")
    elif kind == "shell":
        operation["command"] = "unused"
    else:
        operation.update(image=_IMAGE, command="unused")
    run = {kind: operation}
    if return_mode is not None:
        run["return"] = return_mode
    plan = compile_sandbox_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "sandbox-recovery",
                "version": "1.0.0",
            },
            "do": [{"action": {"run": run}}],
        },
        sandbox=sandbox,
    )
    first = services()
    engine = engine_type()
    if require_native and not (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    ):
        first.close()
        pytest.skip("actual engine SDK is unavailable in this independent environment")
    await engine.initialize(first)
    handle = first.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    execute = engine.executor._execute_sandbox
    reached = asyncio.Event()
    expected_error: dict[str, Any] | None = None

    async def interrupted(request: SandboxExecutionRequest, state: Any) -> Any:
        nonlocal expected_error
        try:
            result = await execute(request, state)
        except OwaError as exc:
            expected_error = exc.as_dict()
            reached.set()
            await asyncio.Event().wait()
            raise
        reached.set()
        await asyncio.Event().wait()
        return result

    engine.executor._execute_sandbox = interrupted
    running = asyncio.create_task(engine.invoke(plan, handle, {"request": "original"}))
    try:
        await asyncio.wait_for(
            executor.committed.wait() if outcome == "pending" else reached.wait(), 15
        )
        assert handle.status == "running" and len(executor.requests) == 1
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        first.close()
    restarted = services()
    recovered = engine_type()
    await recovered.initialize(restarted)
    if changed_request:
        execute_recovered = recovered.executor._execute_sandbox

        async def changed(request: SandboxExecutionRequest, state: Any) -> Any:
            return await execute_recovered(replace(request, arguments=("changed",)), state)

        recovered.executor._execute_sandbox = changed
    loaded = restarted.invocations.get(handle.invocation_id)
    assert loaded is not None and loaded.status == "running"
    try:
        result = await asyncio.wait_for(recovered.resume(loaded, {}, plan), 15)
        assert len(executor.requests) == 1, (
            "sandbox replay must not repeat a known or uncertain dispatch"
        )
        if changed_request or outcome == "pending":
            assert result.status == "faulted" and result.error is not None
            assert (
                "differs" in result.error["message"]
                if changed_request
                else "unresolved" in result.error["message"]
            )
        elif outcome == "fault":
            assert expected_error is not None
            assert result.status == "faulted" and result.error == {
                **expected_error,
                "details": {**expected_error["details"], "instance": "/do/0/action"},
            }
        else:
            expected = {"code": 0, "stdout": "known output", "stderr": ""}
            projection = (
                expected
                if return_mode == "all"
                else None
                if return_mode == "none"
                else expected[return_mode or "stdout"]
            )
            assert result.status == "completed" and result.output == projection
            assert type(result.output) is type(projection)
            duplicate = await recovered.resume(loaded, {}, plan)
            assert duplicate.output == result.output and len(executor.requests) == 1
            if require_native:
                assert restarted.invocations.connection.kind == backend
                assert restarted.effect_journal.connection.kind == backend
                assert any(native_snapshot(restarted, loaded, backend).values())
    finally:
        await recovered.shutdown()
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("return_mode", ["stdout", "stderr", "code", "all", "none"])
async def test_actual_native_sandbox_return_projection_reopens_known_receipt_without_dispatch(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str, return_mode: str
) -> None:
    await test_sandbox_outcome_or_uncertain_intent_prevents_replay_dispatch(
        tmp_path, engine_name, engine_type, backend, "script", "value", False, return_mode, True
    )
