from __future__ import annotations

import asyncio

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine

ENGINE_CASES = [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)]


def _workflow(tasks):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "operator-tests",
            "name": "lifecycle-matrix",
            "version": "1.0.0",
        },
        "do": tasks,
    }


async def _until(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("lifecycle condition was not reached")


def _handle(services, engine_name, plan):
    return services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine_name", "engine_type"), ENGINE_CASES)
@pytest.mark.parametrize(
    "scenario",
    ["success", "controlled_fault", "retry", "timeout"],
)
async def test_operator_matrix_terminal_outcomes(tmp_path, engine_name, engine_type, scenario):
    model = FakeModel({"response": "ok"}, failures=1 if scenario == "retry" else 0)
    from open_workflow_agent.services import RuntimeServices

    services = RuntimeServices(
        RuntimeConfig(), model=model, database_root=tmp_path / engine_name / scenario
    )
    engine = engine_type()
    await engine.initialize(services)
    workflows = {
        "success": _workflow([{"finish": {"set": {"done": True}}}]),
        "controlled_fault": _workflow([{"fail": {"raise": {"error": "controlled"}}}]),
        "retry": _workflow(
            [
                {
                    "retry_call": {
                        "try": [
                            {
                                "model": {
                                    "call": "llm:1.0.0@default",
                                    "with": {"prompt": "retry"},
                                }
                            }
                        ],
                        "catch": {"retry": {"limit": {"attempt": {"count": 1}}}},
                    }
                }
            ]
        ),
        "timeout": _workflow(
            [{"pause": {"wait": {"seconds": 1}, "timeout": {"after": {"milliseconds": 1}}}}]
        ),
    }
    plan = compile_workflow(workflows[scenario])
    handle = _handle(services, engine_name, plan)
    result = await engine.invoke(plan, handle, {})
    expected = "faulted" if scenario in {"controlled_fault", "timeout"} else "completed"
    assert result.status == expected
    assert handle.status == expected
    assert any(event.event_type == "WorkflowStarted" for event in services.events.events)
    assert services.events.events[-1].event_type in {"WorkflowCompleted", "WorkflowFaulted"}
    services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine_name", "engine_type"), ENGINE_CASES)
async def test_operator_matrix_wait_resume_and_cancellation(tmp_path, engine_name, engine_type):
    from open_workflow_agent.services import RuntimeServices

    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    engine = engine_type()
    await engine.initialize(services)
    plan = compile_workflow(
        _workflow(
            [
                {"pause": {"wait": {"seconds": 5}}},
                {"finish": {"set": {"done": True}}},
            ]
        )
    )
    handle = _handle(services, engine_name, plan)
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    await _until(lambda: handle.status == "waiting")
    resumed = await engine.resume(handle, {"continue": True}, plan)
    assert resumed.status == "completed"
    assert (await running).status == "completed"
    assert [event.event_type for event in services.events.events].count("WorkflowWaiting") == 1
    assert [event.event_type for event in services.events.events].count("WorkflowResumed") == 1

    cancel_plan = compile_workflow(_workflow([{"pause": {"wait": {"seconds": 5}}}]))
    cancel_handle = _handle(services, engine_name, cancel_plan)
    cancelled_run = asyncio.create_task(engine.invoke(cancel_plan, cancel_handle, {}))
    await _until(lambda: cancel_handle.status == "waiting")
    cancelled = await engine.cancel(cancel_handle, operation_id="operator-cancel")
    assert cancelled.status == "cancelled"
    assert (await cancelled_run).status == "cancelled"
    duplicate = await engine.cancel(cancel_handle, operation_id="operator-cancel")
    assert duplicate.status == "cancelled"
    services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine_name", "engine_type"), ENGINE_CASES)
async def test_operator_matrix_cancellation_while_running(tmp_path, engine_name, engine_type):
    from open_workflow_agent.services import RuntimeServices

    started = asyncio.Event()

    async def slow(_prompt):
        started.set()
        await asyncio.sleep(30)
        return {"never": True}

    services = RuntimeServices(
        RuntimeConfig(), model=FakeModel(slow), database_root=tmp_path / engine_name
    )
    engine = engine_type()
    await engine.initialize(services)
    plan = compile_workflow(
        _workflow(
            [
                {
                    "slow": {
                        "call": "llm:1.0.0@default",
                        "with": {"prompt": "slow"},
                    }
                }
            ]
        )
    )
    handle = _handle(services, engine_name, plan)
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    await started.wait()
    result = await engine.cancel(handle, operation_id="running-cancel")
    assert result.status == "cancelled"
    assert (await running).status == "cancelled"
    services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine_name", "engine_type"), ENGINE_CASES)
async def test_operator_matrix_restart_resume_reuses_side_effect_operation(
    tmp_path, engine_name, engine_type
):
    from open_workflow_agent.services import RuntimeServices

    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Idempotency-Key"])
        return httpx.Response(200, json={"ok": True})

    workflow = _workflow(
        [
            {
                "side_effect": {
                    "call": "http",
                    "with": {
                        "method": "POST",
                        "endpoint": "https://service.test/side-effect",
                        "body": {"operation": "once-or-more"},
                    },
                }
            },
            {"pause": {"wait": {"seconds": 5}}},
            {"finish": {"set": {"done": True}}},
        ]
    )
    config = RuntimeConfig()
    root = tmp_path / engine_name
    first_services = RuntimeServices(config, model=FakeModel(), database_root=root)
    first_services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    first_services.tools.protocols = first_services.protocols
    first_engine = engine_type()
    await first_engine.initialize(first_services)
    plan = compile_workflow(workflow)
    handle = _handle(first_services, engine_name, plan)
    running = asyncio.create_task(first_engine.invoke(plan, handle, {}))
    await _until(lambda: handle.status == "waiting")
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    first_services.close()

    restarted_services = RuntimeServices(config, model=FakeModel(), database_root=root)
    restarted_services.protocols = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(handler))
    )
    restarted_services.tools.protocols = restarted_services.protocols
    restarted_engine = engine_type()
    await restarted_engine.initialize(restarted_services)
    persisted = restarted_services.invocations.get(handle.invocation_id)
    assert persisted is not None
    resumed = await restarted_engine.resume(persisted, {"continue": True}, plan)
    assert resumed.status == "completed"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    duplicate_resume = await restarted_engine.resume(persisted, {"duplicate": True}, plan)
    assert duplicate_resume.status == "completed"
    assert len(calls) == 2
    restarted_services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine_name", "engine_type"), ENGINE_CASES)
async def test_operator_matrix_retry_reuses_side_effect_operation(
    tmp_path, engine_name, engine_type
):
    from open_workflow_agent.services import RuntimeServices

    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Idempotency-Key"])
        if len(calls) == 1:
            return httpx.Response(503, json={"retry": True})
        return httpx.Response(200, json={"ok": True})

    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    services.tools.protocols = services.protocols
    engine = engine_type()
    await engine.initialize(services)
    plan = compile_workflow(
        _workflow(
            [
                {
                    "retry_side_effect": {
                        "try": [
                            {
                                "side_effect": {
                                    "call": "http",
                                    "with": {
                                        "method": "POST",
                                        "endpoint": "https://service.test/retry",
                                        "body": {"operation": "retry-once"},
                                    },
                                }
                            }
                        ],
                        "catch": {
                            "retry": {"limit": {"attempt": {"count": 1}}},
                        },
                    }
                }
            ]
        )
    )
    handle = _handle(services, engine_name, plan)
    result = await engine.invoke(plan, handle, {})
    assert result.status == "completed"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    retry_event = next(
        event for event in services.events.events if event.event_type == "TaskRetried"
    )
    assert retry_event.task_reference == "/do/0/side_effect"
    assert retry_event.operation_id == f"{handle.invocation_id}:/do/0/side_effect"
    services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine_name", "engine_type"), ENGINE_CASES)
async def test_operator_matrix_timeout_and_cancellation_preserve_side_effect_key(
    tmp_path, engine_name, engine_type
):
    from open_workflow_agent.services import RuntimeServices

    timeout_calls: list[str] = []

    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        timeout_calls.append(request.headers["Idempotency-Key"])
        await asyncio.sleep(30)
        return httpx.Response(200, json={"never": True})

    timeout_services = RuntimeServices(
        RuntimeConfig(), model=FakeModel(), database_root=tmp_path / "timeout"
    )
    timeout_services.protocols = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(timeout_handler))
    )
    timeout_services.tools.protocols = timeout_services.protocols
    timeout_engine = engine_type()
    await timeout_engine.initialize(timeout_services)
    timeout_plan = compile_workflow(
        _workflow(
            [
                {
                    "timed_side_effect": {
                        "call": "http",
                        "timeout": {"after": {"milliseconds": 1}},
                        "with": {
                            "method": "POST",
                            "endpoint": "https://service.test/timeout",
                            "body": {"operation": "timeout"},
                        },
                    }
                }
            ]
        )
    )
    timeout_handle = _handle(timeout_services, engine_name, timeout_plan)
    timeout_result = await timeout_engine.invoke(timeout_plan, timeout_handle, {})
    assert timeout_result.status == "faulted"
    assert len(timeout_calls) == 1
    timeout_key = timeout_calls[0]

    cancellation_started = asyncio.Event()
    cancellation_calls: list[str] = []

    async def cancellation_handler(request: httpx.Request) -> httpx.Response:
        cancellation_calls.append(request.headers["Idempotency-Key"])
        cancellation_started.set()
        await asyncio.sleep(30)
        return httpx.Response(200, json={"never": True})

    cancellation_services = RuntimeServices(
        RuntimeConfig(), model=FakeModel(), database_root=tmp_path / "cancellation"
    )
    cancellation_services.protocols = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(cancellation_handler))
    )
    cancellation_services.tools.protocols = cancellation_services.protocols
    cancellation_engine = engine_type()
    await cancellation_engine.initialize(cancellation_services)
    cancellation_plan = compile_workflow(
        _workflow(
            [
                {
                    "cancelled_side_effect": {
                        "call": "http",
                        "with": {
                            "method": "POST",
                            "endpoint": "https://service.test/cancel",
                            "body": {"operation": "cancel"},
                        },
                    }
                }
            ]
        )
    )
    cancellation_handle = _handle(cancellation_services, engine_name, cancellation_plan)
    invocation = asyncio.create_task(
        cancellation_engine.invoke(cancellation_plan, cancellation_handle, {})
    )
    await cancellation_started.wait()
    cancelled = await cancellation_engine.cancel(cancellation_handle, operation_id="cancel-key")
    assert cancelled.status == "cancelled"
    assert (await invocation).status == "cancelled"
    assert len(cancellation_calls) == 1
    assert (
        cancellation_calls[0] == f"{cancellation_handle.invocation_id}:/do/0/cancelled_side_effect"
    )
    assert timeout_key != cancellation_calls[0]
    timeout_services.close()
    cancellation_services.close()


@pytest.mark.asyncio
async def test_operator_matrix_shared_events_are_equivalent(tmp_path):
    from open_workflow_agent.services import RuntimeServices

    signatures = []
    for engine_name, engine_type in ENGINE_CASES:
        services = RuntimeServices(
            RuntimeConfig(), model=FakeModel(), database_root=tmp_path / engine_name
        )
        engine = engine_type()
        await engine.initialize(services)
        plan = compile_workflow(
            _workflow(
                [
                    {"pause": {"wait": {"seconds": 5}}},
                    {"finish": {"set": {"done": True}}},
                ]
            )
        )
        handle = _handle(services, engine_name, plan)
        invocation = asyncio.create_task(engine.invoke(plan, handle, {}))
        await _until(lambda handle=handle: handle.status == "waiting")
        result = await engine.resume(handle, {"continue": True}, plan)
        assert (await invocation).status == "completed"
        signatures.append(
            (
                result.status,
                result.output,
                [
                    (
                        event.event_type,
                        event.status,
                        event.task_reference,
                        event.reason,
                        event.attempt,
                        event.progress,
                    )
                    for event in services.events.events
                ],
            )
        )
        services.close()
    assert signatures[0] == signatures[1]
