"""Common recovery inputs must keep their JSON meaning on every adapter."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import OwaError, WorkflowExecutionError
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def _services(tmp_path, backend, model):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    return RuntimeServices(config, model=model, database_root=None if datasource else tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "workflow_input",
    [
        pytest.param(None, id="raw-null"),
        pytest.param(False, id="raw-false"),
        pytest.param({"original": "kept"}, id="raw-object"),
        pytest.param([1, 2], id="raw-array"),
    ],
)
@pytest.mark.parametrize(
    "resume_input",
    [
        pytest.param(None, id="null"),
        pytest.param(False, id="false"),
        pytest.param(0, id="zero"),
        pytest.param("", id="empty-string"),
        pytest.param([], id="empty-array"),
        pytest.param({}, id="empty-object"),
    ],
)
async def test_fresh_wait_resume_preserves_all_json_inputs(
    tmp_path, engine_name, engine_type, resume_input, workflow_input, backend
):
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "checkpoint-recovery",
                "version": "1.0.0",
            },
            "input": {"from": "${ {wrapped: .} }"},
            "do": [
                {"pause": {"wait": {"seconds": 300}}},
                {
                    "report": {
                        "set": {
                            "resumed": "${ $resume_input }",
                            "original": "${ $workflow.input }",
                            "context": "${ $context }",
                        }
                    }
                },
            ],
        }
    )
    services = _services(tmp_path, backend, FakeModel())
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, workflow_input))
    try:
        waiting = await asyncio.wait_for(asyncio.shield(running), timeout=10)
        assert waiting.status == "waiting", waiting.error
        assert handle.invocation_id not in engine._active
        assert handle.invocation_id not in engine._ownership_tasks
        assert handle._execution_lease is None
        assert services.invocations.read_input(handle) == workflow_input
        assert services.model.calls == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = _services(tmp_path, backend, FakeModel())
    resumed_engine = engine_type()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None and persisted.status == "waiting"
    try:
        try:
            result = await asyncio.wait_for(
                resumed_engine.resume(persisted, resume_input, plan), timeout=10
            )
        except TimeoutError:
            pytest.fail("a submitted JSON resume value must wake the recovered wait")
        assert result.status == "completed", result.error
        assert result.output == {
            "resumed": resume_input,
            "original": workflow_input,
            "context": {"wrapped": workflow_input},
        }
        assert restarted.model.calls == []
        replay = await resumed_engine.resume(persisted, {"duplicate": True}, plan)
        assert replay.status == "completed" and replay.output == result.output
    finally:
        await resumed_engine.shutdown()
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_successful_null_resume_does_not_repeat_completed_execution(
    tmp_path, engine_name, engine_type, backend
):
    calls = []

    async def action(request):
        calls.append(request.headers["Idempotency-Key"])
        return httpx.Response(200, json={"committed": True})

    def services():
        result = _services(tmp_path, backend, FakeModel())
        result.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(action)))
        result.tools.protocols = result.protocols
        return result

    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "null-completion-recovery",
                "version": "1.0.0",
            },
            "do": [
                {"pause": {"wait": {"seconds": 300}}},
                {
                    "action": {
                        "call": "http",
                        "with": {"method": "POST", "endpoint": "https://service.test/action"},
                    }
                },
            ],
            "output": {"as": "${ null }"},
        }
    )
    first = services()
    engine = engine_type()
    await engine.initialize(first)
    handle = first.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        waiting = await asyncio.wait_for(asyncio.shield(running), timeout=10)
        assert waiting.status == "waiting", waiting.error
        assert handle.invocation_id not in engine._active
        assert handle.invocation_id not in engine._ownership_tasks
        assert handle._execution_lease is None
        assert first.invocations.read_input(handle) == {}
        assert calls == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        first.close()

    reopened = services()
    recovered = engine_type()
    await recovered.initialize(reopened)
    persisted = reopened.invocations.get(handle.invocation_id)
    assert persisted is not None and persisted.status == "waiting"
    try:
        result = await asyncio.wait_for(recovered.resume(persisted, {"resume": True}, plan), 10)
        assert result.status == "completed", result.error
        assert result.output is None and len(calls) == 1
        duplicate = await recovered.resume(persisted, None, plan)
        assert duplicate.status == "completed" and duplicate.output is None
        assert len(calls) == 1
    finally:
        await recovered.shutdown()
        reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("late_outcome", ["value", "fault"])
async def test_late_native_resume_result_preserves_committed_cancellation(
    tmp_path, engine_name, engine_type, late_outcome, backend
):
    if engine_name not in {"adk", "langgraph"}:
        pytest.skip("optional bridge has no separate native resume boundary")
    services = _services(tmp_path, backend, FakeModel())
    engine = engine_type()
    await engine.initialize(services)
    started = asyncio.Event()
    release = asyncio.Event()

    async def native_completion(*args, **kwargs):
        started.set()
        await release.wait()
        if late_outcome == "fault":
            raise WorkflowExecutionError("late native failure")
        return {"late": True}

    async def native_admission(*args, **kwargs):
        # This fixture injects only finalization, after valid native admission.
        # Actual missing/inconsistent native records have separate SDK probes.
        return None

    engine.native = (
        SimpleNamespace(available=True, resume=native_completion, admit_resume=native_admission)
        if engine_name == "adk"
        else SimpleNamespace(
            task_nodes_available=True, execute_plan=native_completion, admit_resume=native_admission
        )
    )
    plan = compile_workflow()
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    services.invocations.bind_input(handle, {})
    running = asyncio.create_task(engine.resume(handle, {}, plan))
    try:
        await asyncio.wait_for(started.wait(), 10)
        cancelled = await engine.cancel(handle, operation_id="committed-cancellation")
        assert cancelled.status == "cancelled"
        release.set()
        result = await asyncio.wait_for(running, 10)
        assert result.as_dict() == cancelled.as_dict()
        persisted = services.invocations.get(handle.invocation_id)
        assert persisted is not None and persisted.status == "cancelled"
        assert persisted.error == cancelled.error and persisted.output is None
        assert services.model.calls == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
@pytest.mark.parametrize("adapter_boundary", ["interruption", "late-fault", "late-value"])
async def test_completion_record_survives_interruption_before_adapter_finalization(
    tmp_path, engine_name, engine_type, outcome, adapter_boundary, backend
):
    tasks = [{"effect": {"call": "llm:1.0.0@default", "with": {"prompt": "once"}}}]
    if outcome == "fault":
        tasks.append(
            {
                "fail": {
                    "raise": {
                        "error": {
                            "type": "urn:test:recorded-fault",
                            "status": 409,
                            "detail": "retained",
                        }
                    }
                }
            }
        )
    source = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "test",
            "name": "completion-boundary",
            "version": "1.0.0",
        },
        "do": tasks,
    }
    if outcome == "null":
        source["output"] = {"as": "${ null }"}
    plan = compile_workflow(source)
    first = _services(tmp_path, backend, FakeModel({"result": "retained"}))
    engine = engine_type()
    await engine.initialize(first)
    original_execute = engine.executor.execute
    reached_boundary = asyncio.Event()
    observed_error = None

    async def interrupt_finalization(*args, **kwargs):
        nonlocal observed_error
        try:
            output = await original_execute(*args, **kwargs)
        except OwaError as exc:
            observed_error = exc.as_dict()
            if adapter_boundary == "late-fault":
                raise WorkflowExecutionError("native finalization failed") from None
            if adapter_boundary == "late-value":
                return {"native": "late"}
            reached_boundary.set()
            await asyncio.Event().wait()
            raise
        if adapter_boundary == "late-fault":
            raise WorkflowExecutionError("native finalization failed")
        if adapter_boundary == "late-value":
            return {"native": "late"}
        reached_boundary.set()
        await asyncio.Event().wait()
        return output

    engine.executor.execute = interrupt_finalization
    handle = first.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
    try:
        if adapter_boundary == "interruption":
            await asyncio.wait_for(reached_boundary.wait(), 10)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 10)
        else:
            finalized = await asyncio.wait_for(running, 10)
            assert finalized.status == ("faulted" if outcome == "fault" else "completed")
            assert finalized.error == observed_error
            assert finalized.output == ({"result": "retained"} if outcome == "value" else None)
        assert len(first.model.calls) == 1
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        first.close()

    async def forbidden(_prompt):
        raise AssertionError("a completed invocation must not execute its action again")

    reopened = _services(tmp_path, backend, FakeModel(forbidden))
    recovered = engine_type()
    await recovered.initialize(reopened)
    persisted = reopened.invocations.get(handle.invocation_id)
    assert persisted is not None
    try:
        result = await recovered.resume(persisted, {"different": True}, plan)
        assert result.status == ("faulted" if outcome == "fault" else "completed")
        assert result.error == observed_error
        assert result.output == ({"result": "retained"} if outcome == "value" else None)
        assert reopened.model.calls == []
        duplicate = await recovered.resume(persisted, None, plan)
        assert duplicate.as_dict() == result.as_dict()
    finally:
        await recovered.shutdown()
        reopened.close()
