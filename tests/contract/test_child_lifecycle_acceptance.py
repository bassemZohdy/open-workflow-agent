"""Child END/context, resolved versions and real worker/fence acceptance."""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.errors import InvocationStateConflict
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.workflow import compile_workflow
from test_native_checkpoint_fencing import _snapshot
from test_synchronous_child_waits import (
    _advance_child,
    _advance_parent,
    _clock,
    _idle,
    _parent,
    _run,
    _services,
    _source,
)

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_waiting_child_end_keeps_exports_private_and_parent_continuation_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    current = [1000.0]
    _clock(monkeypatch, current)
    model = FakeModel({"must": "not-dispatch"})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    services.workflow_catalog.register(
        _source(
            "private-context-child",
            [
                {
                    "prepare": {
                        "set": {"child_value": "${ .value }"},
                        "export": {"as": '${ {child_private:"child-only"} }'},
                    }
                },
                {"pause": {"wait": {"seconds": 60}}},
                {
                    "finish": {
                        "set": {
                            "child_value": "${ .child_value }",
                            "context_seen": "${ $context }",
                        },
                        "then": "end",
                    }
                },
                {"never": {"call": "llm:1.0.0@default"}},
            ],
        )
    )
    child_task = _run("private-context-child")
    child_task["run"]["workflow"]["input"] = {"value": "${ $workflow.input.value }"}
    child_task["output"] = {"as": "${ {child:., parent_context:$context} }"}
    child_task["export"] = {"as": "${ $context + {parent_export:.child.child_value} }"}
    plan = compile_workflow(
        _source(
            "context-parent",
            [
                {
                    "prepare": {
                        "set": {"value": "${ .value }"},
                        "export": {"as": '${ {parent_private:"parent-only"} }'},
                    }
                },
                {"child": child_task},
                {
                    "continue": {
                        "set": {
                            "value": "${ .child.child_value }",
                            "child_context": "${ .child.context_seen }",
                            "parent_context_before_export": "${ .parent_context }",
                            "parent_context": "${ $context }",
                        }
                    }
                },
            ],
        )
    )
    try:
        await engine.initialize(services)
        parent = _parent(services, engine_name, plan)
        result = await engine.invoke(plan, parent, {"value": "supplied"})
        assert result.status == "waiting", result.error
        registration = services.child_waits.for_invocation(parent.invocation_id, plan.fingerprint)[
            0
        ]
        child = services.invocations.get(registration.suspension.child_id)
        _idle(engine, parent, child)
        current[0] = 1060
        await _advance_child(engine, child.invocation_id, current[0])
        await _advance_parent(engine, registration.identity.key)
        saved = services.invocations.get(parent.invocation_id)
        assert saved.status == "completed", saved.error
        assert saved.output == {
            "value": "supplied",
            "child_context": {"child_private": "child-only"},
            "parent_context_before_export": {"parent_private": "parent-only"},
            "parent_context": {"parent_private": "parent-only", "parent_export": "supplied"},
        }
        assert services.invocations.get(child.invocation_id).output == {
            "child_value": "supplied",
            "context_seen": {"child_private": "child-only"},
        }
        assert not model.calls
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_resolved_latest_child_stays_pinned_when_catalog_advances_after_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    current = [1000.0]
    _clock(monkeypatch, current)
    model = FakeModel({"wrong_version": True})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    first_source = _source(
        "versioned-child",
        [{"pause": {"wait": {"seconds": 60}}}, {"finish": {"set": {"version": "one"}}}],
    )
    first_plan = services.workflow_catalog.register(first_source)
    task = _run("versioned-child")
    task["run"]["workflow"]["version"] = "latest"
    plan = compile_workflow(_source("versioned-parent", [{"start": task}]))
    try:
        await engine.initialize(services)
        parent = _parent(services, engine_name, plan)
        result = await engine.invoke(plan, parent, {"original": True})
        assert result.status == "waiting", result.error
        registration = services.child_waits.for_invocation(parent.invocation_id, plan.fingerprint)[
            0
        ]
        child_id, parent_id = registration.suspension.child_id, parent.invocation_id
        child = services.invocations.get(child_id)
        assert (
            child.workflow_version == "1.0.0"
            and child.workflow_fingerprint == first_plan.fingerprint
        )
        await engine.shutdown()
        services.close()
        services = _services(tmp_path, backend, model)
        services.workflow_catalog.register(first_source)
        newer_source = _source("versioned-child", [{"wrong": {"call": "llm:1.0.0@default"}}])
        newer_source["document"]["version"] = "2.0.0"
        newer = services.workflow_catalog.register(newer_source)
        assert (
            services.workflow_catalog.resolve(
                {"namespace": "child-waits", "name": "versioned-child", "version": "latest"}
            ).fingerprint
            == newer.fingerprint
        )
        engine = engine_type()
        await engine.initialize(services)
        parent = services.invocations.get(parent_id)
        current[0] = 1020
        assert (await engine.resume(parent, {"changed": True}, plan)).status == "waiting"
        current[0] = 1060
        await _advance_child(engine, child_id, current[0])
        await _advance_parent(engine, registration.identity.key)
        child = services.invocations.get(child_id)
        parent = services.invocations.get(parent_id)
        assert parent.status == child.status == "completed"
        assert parent.output == child.output == {"version": "one"}
        assert (
            child.workflow_version == "1.0.0"
            and child.workflow_fingerprint == first_plan.fingerprint
        )
        assert services.invocations.read_input(child) == {"original": True}
        assert (
            child.parent_invocation_id == parent_id and child.parent_task_reference == "/do/0/start"
        )
        assert child.owner_principal == parent.owner_principal == "principal"
        assert not model.calls
    finally:
        await engine.shutdown()
        services.close()


def _action(phase: str) -> dict[str, Any]:
    return {
        "call": "http",
        "with": {
            "method": "POST",
            "endpoint": "https://child.test/action",
            "body": {"phase": phase},
        },
    }


async def _ready_tree(
    path: Path,
    backend: str,
    engine_name: str,
    engine_type: Any,
    handler: Callable[[str, httpx.Request], Awaitable[httpx.Response]],
) -> tuple[Any, ...]:
    services = [_services(path, backend, FakeModel()), _services(path, backend, FakeModel())]
    engines = [engine_type(), engine_type()]
    child_source = _source(
        "worker-child", [{"pause": {"wait": {"seconds": 60}}}, {"after": _action("child")}]
    )
    plan = compile_workflow(
        _source("worker-parent", [{"start": _run("worker-child")}, {"after": _action("parent")}])
    )
    for index, (engine, service) in enumerate(zip(engines, services, strict=True)):
        worker = str(index)

        async def action(request: httpx.Request, worker: str = worker) -> httpx.Response:
            return await handler(worker, request)

        service.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(action)))
        service.tools.protocols = service.protocols
        service.workflow_catalog.register(child_source)
        engine._execution_ownership_seconds = 600
        await engine.initialize(service)
    parent = _parent(services[0], engine_name, plan)
    result = await engines[0].invoke(plan, parent, {"original": True})
    assert result.status == "waiting", result.error
    registration = services[0].child_waits.for_invocation(parent.invocation_id, plan.fingerprint)[0]
    return services, engines, parent, registration, plan


def _wake_count(services: Any, parent_id: str) -> int:
    rows = services.effect_journal.connection.execute(
        "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?", (parent_id,)
    ).fetchall()
    return sum(json.loads(row[0])["operation"] == "policy:child-wake:v1" for row in rows)


async def _stop(services: list[Any], engines: list[Any]) -> None:
    for engine in engines:
        await engine.shutdown()
    for service in services:
        service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_two_runtime_child_wait_dispatchers_commit_one_wake_and_one_parent_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    current = [1000.0]
    _clock(monkeypatch, current)
    entered, release = asyncio.Event(), asyncio.Event()
    calls: list[tuple[str, str]] = []

    async def action(worker: str, request: httpx.Request) -> httpx.Response:
        phase = json.loads(request.content)["phase"]
        calls.append((worker, phase))
        if phase == "parent":
            entered.set()
            await release.wait()
        return httpx.Response(200, json={"accepted": True})

    services, engines, parent, registration, plan = await _ready_tree(
        tmp_path, backend, engine_name, engine_type, action
    )
    try:
        current[0] = 1060
        await _advance_child(engines[0], registration.suspension.child_id, current[0])
        dispatchers = [engine._timer_dispatcher.child_waits for engine in engines]
        await asyncio.gather(
            *(dispatcher.tick(run_key=registration.identity.key) for dispatcher in dispatchers)
        )
        await asyncio.wait_for(entered.wait(), timeout=30)
        assert [phase for _, phase in calls] == ["child", "parent"]
        assert _wake_count(services[0], parent.invocation_id) == 1
        loser = 1 - int(calls[-1][0])
        contender = services[loser].invocations.get(parent.invocation_id)
        before = contender._persisted_payload
        with pytest.raises(InvocationStateConflict):
            await engines[loser].resume(contender, {"wrong": True}, plan)
        assert services[loser].invocations.get(parent.invocation_id)._persisted_payload == before
        release.set()
        async with asyncio.timeout(30):
            while any(dispatcher._workers for dispatcher in dispatchers):
                await asyncio.sleep(0.01)
        assert all(
            isinstance(error, InvocationStateConflict)
            for dispatcher in dispatchers
            for error in dispatcher._errors
        )
        parent = services[0].invocations.get(parent.invocation_id)
        assert parent.status == "completed" and parent.output == {"accepted": True}
        assert (
            services[0]
            .children.connection.execute(
                "SELECT COUNT(*) FROM child_workflow_bindings WHERE root_id=?",
                (parent.invocation_id,),
            )
            .fetchone()[0]
            == 1
        )
        immutable = services[0].effect_journal.get(
            replace(registration.identity, operation="policy:child-wake:v1")
        )
        for dispatcher in dispatchers:
            await dispatcher.tick(run_key=registration.identity.key)
        async with asyncio.timeout(30):
            while any(dispatcher._workers for dispatcher in dispatchers):
                await asyncio.sleep(0.01)
        assert [phase for _, phase in calls] == ["child", "parent"]
        assert _wake_count(services[0], parent.invocation_id) == 1
        assert (
            services[0].effect_journal.get(
                replace(registration.identity, operation="policy:child-wake:v1")
            )
            == immutable
        )
    finally:
        release.set()
        await _stop(services, engines)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_actual_parent_takeover_fences_old_child_wake_and_native_checkpoint_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    native = engine_type().native
    if not (native.available if engine_name == "adk" else native.task_nodes_available):
        pytest.skip("actual principal native SDK environment is required")
    current = [1000.0]
    _clock(monkeypatch, current)
    calls: list[str] = []

    async def action(_worker: str, request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["phase"])
        return httpx.Response(200, json={"accepted": True})

    services, engines, parent, registration, plan = await _ready_tree(
        tmp_path, backend, engine_name, engine_type, action
    )
    writer_entered, writer_release = asyncio.Event(), asyncio.Event()
    armed = True
    successor: Any = None

    def gated(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def write(self: Any, *args: Any, **kwargs: Any) -> Any:
            if armed:
                writer_entered.set()
                await writer_release.wait()
            return await original(self, *args, **kwargs)

        return write

    try:
        current[0] = 1060
        await _advance_child(engines[0], registration.suspension.child_id, current[0])
        module = importlib.import_module(f"open_workflow_agent_{engine_name}.native")
        sdk = getattr(
            module,
            (
                "_FencedDatabaseSessionService"
                if backend == "postgresql"
                else "_ClosingSqliteSessionService"
            )
            if engine_name == "adk"
            else (
                "_FencedAsyncPostgresSaver"
                if backend == "postgresql"
                else "_FencedAsyncSqliteSaver"
            ),
        )
        for method in ("append_event",) if engine_name == "adk" else ("aput", "aput_writes"):
            monkeypatch.setattr(sdk, method, gated(getattr(sdk, method)))
        dispatcher = engines[0]._timer_dispatcher.child_waits
        await dispatcher.tick(run_key=registration.identity.key)
        await asyncio.wait_for(writer_entered.wait(), timeout=30)
        old_lease = engines[0]._execution_leases[parent.invocation_id]
        old_parent = services[0].invocations.get(parent.invocation_id)
        old_parent._execution_lease = old_lease
        before = old_parent._persisted_payload
        native_before = _snapshot(services[0], old_parent, backend)
        assert _wake_count(services[0], parent.invocation_id) == 1
        services[0].execution_ownership.connection.execute(
            "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
            (parent.invocation_id,),
        )
        services[0].execution_ownership.connection.commit()
        new_parent = services[1].invocations.get(parent.invocation_id)
        successor = services[1].execution_ownership.acquire(new_parent, seconds=600)
        new_parent._execution_lease = successor
        assert successor.generation > old_lease.generation and successor.owner != old_lease.owner
        with pytest.raises(InvocationStateConflict):
            services[0].child_waits.wake(registration, old_parent, now=current[0])
        with pytest.raises(InvocationStateConflict):
            services[0].child_waits.consume(registration, old_parent)
        with pytest.raises(InvocationStateConflict):
            services[0].child_waits.arm(
                registration.identity,
                old_parent,
                services[0].invocations.get(registration.suspension.child_id),
                workflow_source=plan.source,
                deadline_at=None,
            )
        writer_release.set()
        async with asyncio.timeout(30):
            while dispatcher._workers:
                await asyncio.sleep(0.01)
        assert dispatcher._errors and all(
            isinstance(error, InvocationStateConflict) for error in dispatcher._errors
        )
        assert services[1].invocations.get(parent.invocation_id)._persisted_payload == before
        assert _snapshot(services[0], old_parent, backend) == native_before
        assert calls == ["child"]
        known = services[1].child_waits.wake(registration, new_parent, now=current[0])
        assert known is not None and known["reason"] == "child_terminal"
        assert _wake_count(services[1], parent.invocation_id) == 1
        services[1].execution_ownership.release(successor)
        successor = None
        new_parent._execution_lease = None
        armed = False
        await _advance_parent(engines[1], registration.identity.key)
        parent = services[1].invocations.get(parent.invocation_id)
        assert parent.status == "completed" and parent.output == {"accepted": True}
        assert calls == ["child", "parent"]
        assert _wake_count(services[1], parent.invocation_id) == 1
    finally:
        writer_release.set()
        if successor is not None:
            services[1].execution_ownership.release(successor)
        await _stop(services, engines)
