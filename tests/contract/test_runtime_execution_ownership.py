"""Runtime admission and lease supervision share the common ownership boundary."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateConflict, InvocationStateError
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import WorkflowPlan, compile_workflow


def _services(root: Path, backend: str) -> RuntimeServices:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    return RuntimeServices(config, model=FakeModel(), database_root=None if datasource else root)


def _plan() -> WorkflowPlan:
    return compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "ownership",
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


def _blocked_protocol(service: RuntimeServices) -> tuple[asyncio.Event, asyncio.Event, list[Any]]:
    entered, finish = asyncio.Event(), asyncio.Event()
    calls: list[Any] = []

    async def request(message: httpx.Request) -> httpx.Response:
        calls.append(json.loads(message.content))
        entered.set()
        await finish.wait()
        return httpx.Response(200, json={"accepted": True})

    service.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(request)))
    service.tools.protocols = service.protocols
    return entered, finish, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("operation", ["invoke", "resume"])
async def test_other_runtime_rejects_execution_before_dispatch_or_common_state_change(
    tmp_path: Path, engine_name: str, engine_type: type[Any], backend: str, operation: str
) -> None:
    first, second = _services(tmp_path, backend), _services(tmp_path, backend)
    engines = [engine_type(), engine_type()]
    entered, finish, calls = _blocked_protocol(first)
    _, _, other_calls = _blocked_protocol(second)
    plan = _plan()
    running = None
    try:
        for engine, service in zip(engines, (first, second), strict=True):
            await engine.initialize(service)
        handle = first.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        running = asyncio.create_task(engines[0].invoke(plan, handle, {"original": True}))
        await asyncio.wait_for(entered.wait(), 15)
        loaded = second.invocations.get(handle.invocation_id)
        assert loaded is not None
        before = loaded._persisted_payload
        with pytest.raises(InvocationStateConflict) as error:
            if operation == "invoke":
                await engines[1].invoke(plan, loaded, {"wrong": True})
            else:
                await engines[1].resume(loaded, {"wrong": True}, plan)
        assert error.value.status_code == 409
        assert loaded._persisted_payload == before
        current = second.invocations.get(handle.invocation_id)
        assert current is not None and current._persisted_payload == before
        assert calls == [{"original": True}] and not other_calls
        finish.set()
        result = await asyncio.wait_for(running, 15)
        assert result.status == "completed" and result.output == {"accepted": True}
        assert (await engines[1].resume(loaded, None, plan)).as_dict() == result.as_dict()
    finally:
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        for engine in engines:
            await engine.shutdown()
        first.close()
        second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_same_runtime_coalesces_invocation_without_another_dispatch(
    tmp_path: Path, engine_name: str, engine_type: type[Any]
) -> None:
    service = _services(tmp_path, "sqlite")
    engine = engine_type()
    entered, finish, calls = _blocked_protocol(service)
    plan = _plan()
    tasks = []
    try:
        await engine.initialize(service)
        handle = service.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        tasks.append(asyncio.create_task(engine.invoke(plan, handle, {"original": True})))
        await asyncio.wait_for(entered.wait(), 15)
        loaded = service.invocations.get(handle.invocation_id)
        assert loaded is not None
        tasks.append(asyncio.create_task(engine.invoke(plan, loaded, {"different": True})))
        await asyncio.sleep(0)
        assert not tasks[1].done() and calls == [{"original": True}]
        finish.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 15)
        assert results[0].as_dict() == results[1].as_dict()
        assert calls == [{"original": True}]
        assert not engine._execution_leases and not engine._active
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.shutdown()
        service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_lease_loss_drains_owner_and_coalesced_callers_without_a_new_state_write(
    tmp_path: Path, engine_name: str, engine_type: type[Any], backend: str
) -> None:
    first, second = _services(tmp_path, backend), _services(tmp_path, backend)
    engine = engine_type()
    entered, _, calls = _blocked_protocol(first)
    renew_now = asyncio.Event()

    async def renewal_boundary() -> None:
        await renew_now.wait()
        renew_now.clear()

    engine._wait_for_ownership_renewal = renewal_boundary
    plan = _plan()
    tasks = []
    try:
        await engine.initialize(first)
        handle = first.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        tasks.append(asyncio.create_task(engine.invoke(plan, handle, {"original": True})))
        await asyncio.wait_for(entered.wait(), 15)
        loaded = second.invocations.get(handle.invocation_id)
        assert loaded is not None
        before = loaded._persisted_payload
        tasks.append(asyncio.create_task(engine.resume(loaded, {"resume": True}, plan)))
        store = second.execution_ownership
        store.connection.execute(
            "UPDATE invocation_ownership SET expires_at = 1 WHERE invocation_id = ?",
            (handle.invocation_id,),
        )
        store.connection.commit()
        successor = store.acquire(loaded)
        renew_now.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 15)
        assert all(isinstance(error, InvocationStateConflict) for error in results)
        current = second.invocations.get(handle.invocation_id)
        assert current is not None and current._persisted_payload == before
        assert calls == [{"original": True}]
        store.verify(successor)
        assert not engine._execution_leases and not engine._active
        store.release(successor)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.shutdown()
        first.close()
        second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("failure", [False, True])
async def test_heartbeat_renews_ownership_or_stops_on_storage_failure(
    tmp_path: Path,
    engine_name: str,
    engine_type: type[Any],
    backend: str,
    failure: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _services(tmp_path, backend)
    engine = engine_type()
    entered, finish, calls = _blocked_protocol(service)
    renew_now, renewed = asyncio.Event(), asyncio.Event()
    real_renew = service.execution_ownership.renew

    async def renewal_boundary() -> None:
        await renew_now.wait()
        renew_now.clear()

    def renew(lease: Any, *, seconds: float = 30) -> Any:
        if failure:
            raise RuntimeError("private-storage-credential")
        result = real_renew(lease, seconds=seconds)
        renewed.set()
        return result

    engine._wait_for_ownership_renewal = renewal_boundary
    monkeypatch.setattr(service.execution_ownership, "renew", renew)
    plan = _plan()
    running = None
    try:
        await engine.initialize(service)
        handle = service.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
        await asyncio.wait_for(entered.wait(), 15)
        before = handle._persisted_payload
        token = engine._execution_leases[handle.invocation_id]
        renew_now.set()
        if failure:
            with pytest.raises(InvocationStateConflict) as error:
                await asyncio.wait_for(running, 15)
            assert "private-storage-credential" not in json.dumps(error.value.as_dict())
            current = service.invocations.get(handle.invocation_id)
            assert current is not None and current._persisted_payload == before
        else:
            await asyncio.wait_for(renewed.wait(), 15)
            service.execution_ownership.verify(token)
            finish.set()
            result = await asyncio.wait_for(running, 15)
            assert result.status == "completed"
            assert token.owner not in json.dumps(result.as_dict())
        assert calls == [{"original": True}]
        assert not engine._ownership_tasks and not engine._execution_leases
        assert handle._execution_lease is None
    finally:
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_shutdown_drains_execution_and_releases_its_claim(
    tmp_path: Path, engine_name: str, engine_type: type[Any]
) -> None:
    service = _services(tmp_path, "sqlite")
    engine = engine_type()
    entered, _, _ = _blocked_protocol(service)
    plan = _plan()
    running = None
    try:
        await engine.initialize(service)
        handle = service.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
        await asyncio.wait_for(entered.wait(), 15)
        before = handle._persisted_payload
        await engine.shutdown()
        assert running.done() and running.cancelled()
        current = service.invocations.get(handle.invocation_id)
        assert current is not None and current._persisted_payload == before
        assert not engine._ownership_tasks and not engine._execution_leases and not engine._active
        lease = service.execution_ownership.acquire(current)
        service.execution_ownership.release(lease)
    finally:
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
async def test_shutdown_drains_recovery_admission_before_execution_begins(
    tmp_path: Path, engine_name: str, engine_type: type[Any]
) -> None:
    service = _services(tmp_path, "sqlite")
    engine = engine_type()
    entered = asyncio.Event()
    plan = _plan()

    async def verify(_: Any, __: WorkflowPlan) -> Any:
        entered.set()
        await asyncio.Event().wait()

    engine._verify_resume_state = verify
    running = None
    try:
        await engine.initialize(service)
        handle = service.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        service.invocations.bind_input(handle, {"original": True})
        service.invocations.update(handle, status="waiting")
        before = handle._persisted_payload
        running = asyncio.create_task(engine.resume(handle, None, plan))
        await asyncio.wait_for(entered.wait(), 15)
        assert not engine._active and engine._ownership_tasks
        await engine.shutdown()
        assert running.done() and running.cancelled()
        current = service.invocations.get(handle.invocation_id)
        assert current is not None and current._persisted_payload == before
        assert not engine._ownership_tasks and not engine._execution_leases
        token = service.execution_ownership.acquire(current)
        service.execution_ownership.release(token)
    finally:
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("operation", ["invoke", "resume"])
async def test_named_engine_rejects_foreign_identity_without_claim_or_dispatch(
    tmp_path: Path, engine_name: str, engine_type: type[Any], operation: str
) -> None:
    service = _services(tmp_path, "sqlite")
    engine = engine_type()
    _, _, requests = _blocked_protocol(service)
    plan = _plan()
    try:
        await engine.initialize(service)
        handle = service.invocations.create(
            engine="adk" if engine_name != "adk" else "langgraph",
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        service.invocations.bind_input(handle, {"original": True})
        if operation == "resume":
            service.invocations.update(handle, status="waiting")
        before = handle._persisted_payload
        with pytest.raises(InvocationStateError, match="different engine"):
            if operation == "invoke":
                await engine.invoke(plan, handle, {"original": True})
            else:
                await engine.resume(handle, None, plan)
        saved = service.invocations.get(handle.invocation_id)
        assert saved is not None and saved._persisted_payload == before
        assert (
            service.execution_ownership.connection.execute(
                "SELECT 1 FROM invocation_ownership WHERE invocation_id=?", (handle.invocation_id,)
            ).fetchone()
            is None
        )
        assert not requests and not engine._active and not engine._execution_leases
    finally:
        await engine.shutdown()
        service.close()
