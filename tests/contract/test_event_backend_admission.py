"""Selected event backend admission precedes any original input or action."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import UnsupportedWorkflowFeature
from open_workflow_agent.events import EventEnvelope, InMemoryEventBus
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def _services(path: Path, backend: str, model: FakeModel) -> RuntimeServices:
    return RuntimeServices(
        RuntimeConfig.model_validate({"events": {"backend": "memory"}})
        if backend == "memory"
        else RuntimeConfig(),
        database_root=path,
        model=model,
        event_bus=InMemoryEventBus() if backend == "custom" else None,
    )


def _source(strategy: dict[str, Any], *, iterator: bool = False) -> dict[str, Any]:
    listener: dict[str, Any] = {"listen": {"to": strategy}}
    if iterator:
        listener["foreach"] = {
            "do": [{"project": {"set": {"value": "${ $item.value }", "index": "${ $index }"}}}]
        }
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "events",
            "name": "backend-admission",
            "version": "1.0.0",
        },
        "do": [
            {
                "earlier-action": {
                    "call": "llm:1.0.0@default",
                    "with": {"prompt": "must not dispatch before admission"},
                }
            },
            {"listen": listener},
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["memory", "custom"])
@pytest.mark.parametrize("operation", ["compile", "invoke", "resume"])
@pytest.mark.parametrize("strategy_name", ["all", "any", "correlate"])
async def test_nondurable_backend_rejects_advanced_listener_before_earlier_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    operation: str,
    strategy_name: str,
) -> None:
    selected: dict[str, Any] = {"with": {"type": "fixture.advanced"}}
    strategy: dict[str, Any]
    if strategy_name == "correlate":
        selected["correlate"] = {"order": {"from": "${ .data.order }", "expect": "chosen"}}
        strategy = {"one": selected}
    else:
        strategy = {strategy_name: [selected]}
    model = FakeModel({"must": "not-dispatch"})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    await engine.initialize(services)
    plan = compile_workflow(_source(strategy))
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        if operation == "compile":
            before = handle._persisted_payload
            with pytest.raises(UnsupportedWorkflowFeature):
                await engine.compile(plan)
            saved = services.invocations.get(handle.invocation_id)
            assert saved is not None and saved._persisted_payload == before
        elif operation == "invoke":
            result = await engine.invoke(plan, handle, {"private": "must-not-be-bound"})
            assert result.status == "faulted" and result.error
            assert result.error["code"] == "unsupported_workflow_feature"
        else:
            original = {"original": "must-remain"}
            lease = services.execution_ownership.acquire(handle, seconds=60)
            try:
                services.recovery_definitions.admit(plan, handle, original, execution_lease=lease)
            finally:
                services.execution_ownership.release(lease)
            native_presence_checks = 0

            async def admitted_native_boundary(_handle: Any) -> None:
                # This fixture isolates the event capability gate, rather than
                # qualifying SDK history. Original common admission is real;
                # actual native presence/corruption has dedicated SDK fixtures.
                nonlocal native_presence_checks
                native_presence_checks += 1

            monkeypatch.setattr(engine, "_preflight_native_continuation", admitted_native_boundary)
            services.invocations.update(handle, status="waiting")
            before = handle._persisted_payload
            with pytest.raises(UnsupportedWorkflowFeature):
                await engine.resume(handle, {"replacement": "must-not-be-bound"}, plan)
            saved = services.invocations.get(handle.invocation_id)
            assert saved is not None and saved._persisted_payload == before
            assert services.invocations.read_input(saved) == original
            assert native_presence_checks == 1
        assert not model.calls
        if operation != "resume":
            assert not services.invocations.connection.execute(
                "SELECT payload FROM invocation_inputs WHERE invocation_id = ?",
                (handle.invocation_id,),
            ).fetchall()
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["memory", "custom"])
async def test_nondurable_one_with_foreach_remains_available(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str
) -> None:
    model = FakeModel({"first": True})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    await engine.initialize(services)
    plan = compile_workflow(_source({"one": {"with": {"type": "fixture.allowed"}}}, iterator=True))
    plan = await engine.compile(plan)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        async with asyncio.timeout(30):
            while handle.status != "waiting":
                if running.done():
                    raise AssertionError(await running)
                await asyncio.sleep(0.01)
        await services.event_bus.publish(
            {"id": "allowed", "type": "fixture.allowed", "data": {"value": 3}},
            default_source="urn:contract:memory",
        )
        result = await asyncio.wait_for(running, timeout=30)
        assert result.status == "completed", result.error
        assert result.output == [{"value": 3, "index": 0}]
        assert len(model.calls) == 1
        assert not services.event_bus.durable
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("kind", ["schema", "declared"])
async def test_schema_catch_uses_release_uri_and_declared_type_remains_unchanged(
    tmp_path: Path, engine_name: str, engine_type: Any, kind: str
) -> None:
    error_type = (
        "https://serverlessworkflow.io/spec/1.0.0/errors/validation"
        if kind == "schema"
        else "urn:contract:declared-error"
    )
    status = 400 if kind == "schema" else 409
    operation = (
        {
            "listen": {"to": {"one": {"with": {"type": "fixture.schema"}}}},
            "foreach": {"output": {"schema": {"document": {"type": "integer"}}}},
        }
        if kind == "schema"
        else {"raise": {"error": {"type": error_type, "status": status}}}
    )
    source = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "events",
            "name": "schema-type",
            "version": "1.0.0",
        },
        "do": [
            {
                "attempt": {
                    "try": [{"validation": operation}],
                    "catch": {
                        "as": "failure",
                        "errors": {"with": {"type": error_type, "status": status}},
                        "do": [
                            {
                                "handled": {
                                    "set": {
                                        "type": "${ $failure.type }",
                                        "status": "${ $failure.status }",
                                    }
                                }
                            }
                        ],
                    },
                }
            }
        ],
    }
    model = FakeModel()
    services = RuntimeServices(RuntimeConfig(), database_root=tmp_path, model=model)
    engine = engine_type()
    await engine.initialize(services)
    plan = compile_workflow(source)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        if kind == "schema":
            await services.event_bus.publish(
                {"id": "invalid", "type": "fixture.schema", "data": {"invalid": True}},
                default_source="urn:contract:schema",
            )
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=30)
        assert result.status == "completed", result.error
        assert result.output == {"type": error_type, "status": status}
        assert not model.calls
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["memory", "custom"])
async def test_nondurable_foreach_in_repeated_listener_dispatches_each_occurrence_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    model = FakeModel(lambda payload: {"received": payload["prompt"]})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    registrations = 0
    original_receive = services.event_bus.receive

    async def receive(strategy: Mapping[str, Any]) -> EventEnvelope:
        nonlocal registrations
        registrations += 1
        return await original_receive(strategy)

    monkeypatch.setattr(services.event_bus, "receive", receive)
    running: asyncio.Task[Any] | None = None
    source = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "events",
            "name": "nondurable-foreach-loop",
            "version": "1.0.0",
        },
        "do": [
            {
                "loop": {
                    "for": {"each": "turn", "in": "${ [0,1] }"},
                    "do": [
                        {
                            "next": {
                                "listen": {"to": {"one": {"with": {"type": "fixture.next"}}}},
                                "foreach": {
                                    "do": [
                                        {
                                            "consume": {
                                                "call": "llm:1.0.0@default",
                                                "with": {"prompt": "${ $item.value }"},
                                            }
                                        }
                                    ]
                                },
                                "export": {
                                    "as": (
                                        "${ $context + "
                                        "{received: (($context.received // []) + .)} }"
                                    )
                                },
                            }
                        }
                    ],
                }
            },
            {"result": {"set": {"received": "${ $context.received }"}}},
        ],
    }
    try:
        await engine.initialize(services)
        plan = await engine.compile(compile_workflow(source))
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        running = asyncio.create_task(engine.invoke(plan, handle, {}))
        for ordinal, value in enumerate(("first", "second"), start=1):
            async with asyncio.timeout(30):
                while registrations < ordinal:
                    if running.done():
                        raise AssertionError(await running)
                    await asyncio.sleep(0.01)
            await services.event_bus.publish(
                {"id": value, "type": "fixture.next", "data": {"value": value}},
                default_source="urn:contract:memory-loop",
            )
        result = await asyncio.wait_for(running, timeout=30)
        assert result.status == "completed", result.error
        assert result.output == {"received": [{"received": "first"}, {"received": "second"}]}
        assert model.calls == [{"prompt": "first"}, {"prompt": "second"}]
        assert registrations == 2
        assert not services.event_bus.durable
    finally:
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()
