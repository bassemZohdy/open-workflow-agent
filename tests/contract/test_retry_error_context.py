"""Catch and retry predicates must see the configured OWS error variable."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import AgentError, KnowledgeError, MemoryError, ModelError, OwaError
from open_workflow_agent.observability import InMemoryEventSink
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize(
    "case",
    [
        "builtin-status-filter",
        "custom-catch-when",
        "custom-raised-error",
        "custom-retry-when",
        "default-retry-when",
        "custom-retry-except",
        "custom-catch-except",
    ],
)
async def test_catch_and_retry_use_the_same_error_value(
    services: Any, engine_name: str, engine_type: Any, case: str
) -> None:
    model = FakeModel({"success": True}, failures=1)
    services.model = model
    alias = "error" if case in {"builtin-status-filter", "default-retry-when"} else "failure"
    catch: dict[str, Any] = {
        "as": alias,
        "do": [
            {
                "recovered": {
                    "set": {
                        "status": "${ $" + alias + ".status }",
                        "detail": "${ $" + alias + ".detail }",
                    }
                }
            }
        ],
    }
    action: dict[str, Any] = {"call": "llm:1.0.0@default", "with": {"prompt": "value"}}
    expected_output: Any = {"status": 500, "detail": "controlled fake model failure"}
    expected_status = "completed"
    expected_calls = 1
    if case == "builtin-status-filter":
        catch["errors"] = {"with": {"status": 500}}
    elif case == "custom-catch-when":
        catch["when"] = "${ $failure.status == 500 }"
    elif case == "custom-raised-error":
        action = {
            "raise": {
                "error": {
                    "type": "urn:test:controlled",
                    "status": 503,
                    "title": "Controlled",
                    "detail": "transient failure",
                }
            }
        }
        catch["errors"] = {"with": {"type": "urn:test:controlled", "status": 503}}
        catch["when"] = (
            '${ $failure.title == "Controlled" and $failure.detail == "transient failure" }'
        )
        expected_output = {"status": 503, "detail": "transient failure"}
        expected_calls = 0
    elif case in {"custom-retry-when", "default-retry-when"}:
        catch["retry"] = {
            "when": "${ $" + alias + ".status == 500 }",
            "limit": {"attempt": {"count": 1}},
        }
        expected_output = {"success": True}
        expected_calls = 2
    elif case == "custom-retry-except":
        catch["retry"] = {
            "exceptWhen": "${ $failure.status == 500 }",
            "limit": {"attempt": {"count": 1}},
        }
    else:
        catch["exceptWhen"] = "${ $failure.status == 500 }"
        expected_status = "faulted"
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "retry-error-context",
                "version": "1.0.0",
            },
            "do": [{"attempt": {"try": [{"action": action}], "catch": catch}}],
        }
    )
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
    try:
        result = await engine.invoke(plan, handle, {"original": True})
        assert result.status == expected_status, result.error
        if expected_status == "completed":
            assert result.output == expected_output
        else:
            assert result.error == {
                "code": "model_error",
                "message": "controlled fake model failure",
                "details": {"instance": "/do/0/attempt/try/0/action"},
            }
        assert len(model.calls) == expected_calls
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize(
    "error_type", [ModelError, AgentError, KnowledgeError, MemoryError, RuntimeError]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_recorded_provider_error_retains_catch_context_after_restart(
    tmp_path: Path, engine_name: str, engine_type: Any, error_type: type[Exception], backend: str
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    engine = engine_type()
    available = (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    )
    if not available:
        pytest.skip("independent native engine environment is required")

    def fail(_prompt: Any) -> Any:
        raise error_type(
            "controlled provider failure"
            if issubclass(error_type, OwaError)
            else "provider-secret-sentinel"
        )

    model = FakeModel(fail)
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "provider-error-recovery",
                "version": "1.0.0",
            },
            "do": [
                {
                    "attempt": {
                        "try": [
                            {"action": {"call": "llm:1.0.0@default", "with": {"prompt": "value"}}}
                        ],
                        "catch": {
                            "as": "failure",
                            "errors": {"with": {"status": 500}},
                            "when": "${ $failure.status == 500 }",
                            "do": [
                                {
                                    "recovered": {
                                        "set": {
                                            "code": "${ $failure.code }",
                                            "status": "${ $failure.status }",
                                            "detail": "${ $failure.detail }",
                                        }
                                    }
                                }
                            ],
                        },
                    }
                }
            ],
        }
    )
    services = RuntimeServices(config, model=model, database_root=None if datasource else tmp_path)
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    original_call = engine.executor._call
    reached = asyncio.Event()

    async def interrupt_after_receipt(call: str, payload: Any, state: Any) -> Any:
        try:
            return await original_call(call, payload, state)
        except OwaError:
            reached.set()
            await asyncio.Event().wait()
            raise

    engine.executor._call = interrupt_after_receipt
    running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        assert len(model.calls) == 1
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=10)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = RuntimeServices(config, model=model, database_root=None if datasource else tmp_path)
    resumed_engine = engine_type()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None
    try:
        result = await asyncio.wait_for(resumed_engine.resume(persisted, None, plan), timeout=10)
        assert result.status == "completed", result.error
        known = issubclass(error_type, OwaError)
        assert result.output == {
            "code": getattr(error_type, "code", "workflow_execution_error"),
            "status": 500,
            "detail": "controlled provider failure" if known else "task action failed",
        }
        assert len(model.calls) == 1, "known failure receipts must not call the provider again"
        assert "provider-secret-sentinel" not in repr(result.as_dict())
        assert isinstance(restarted.events, InMemoryEventSink)
        assert "provider-secret-sentinel" not in repr(restarted.events.events)
    finally:
        await resumed_engine.shutdown()
        restarted.close()
