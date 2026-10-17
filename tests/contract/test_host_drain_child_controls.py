"""Host interruption preserves recovery; explicit business controls stay terminal."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationCancelled, InvocationStateConflict
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")
ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name("_host_drain_child_worker.py")


def _record(services: RuntimeServices, invocation_id: str) -> ExecutionHandle:
    value = services.invocations.get(invocation_id)
    assert value is not None
    return value


def _source(name: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "document": {"dsl": "1.0.3", "namespace": "host-drain", "name": name, "version": "1.0.0"},
        "do": tasks,
    }


def _services(path: Path, backend: str) -> RuntimeServices:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    return RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else path,
    )


def _parent(services: RuntimeServices, engine_name: str, plan: Any) -> Any:
    return services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="correlation",
        owner_principal="principal",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


def _plans(services: RuntimeServices) -> tuple[Any, Any]:
    child = services.workflow_catalog.register(
        _source(
            "child",
            [
                {
                    "action": {
                        "call": "http",
                        "with": {
                            "method": "POST",
                            "endpoint": "https://child.test/action",
                            "body": "${ $workflow.input }",
                        },
                    }
                }
            ],
        )
    )
    parent = compile_workflow(
        _source(
            "parent",
            [
                {
                    "child": {
                        "run": {
                            "workflow": {
                                "namespace": "host-drain",
                                "name": "child",
                                "version": "1.0.0",
                            }
                        }
                    }
                }
            ],
        )
    )
    return parent, child


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("boundary", ["pending-action", "known-receipt", "translated-callback"])
async def test_host_shutdown_does_not_forge_child_business_cancellation(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    boundary: str,
) -> None:
    services = _services(tmp_path, backend)
    parent_plan, child_plan = _plans(services)
    engine = engine_type()
    await engine.initialize(services)
    parent = _parent(services, engine_name, parent_plan)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []
    child_id: str | None = None
    call_identity: EffectIdentity | None = None
    original_notify = engine.executor._notify_receipt

    async def action(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Idempotency-Key"])
        if boundary != "known-receipt":
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                if boundary == "translated-callback":
                    raise InvocationCancelled("provider callback interrupted") from None
                raise
        return httpx.Response(200, json={"accepted": "child"})

    async def notify(state: Any, identity: EffectIdentity, receipt: Any) -> None:
        nonlocal child_id, call_identity
        await original_notify(state, identity, receipt)
        if identity.operation == "call:http":
            child_id, call_identity = identity.invocation_id, identity
            if boundary == "known-receipt":
                assert receipt.status == "completed"
                entered.set()
                await release.wait()

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(action)))
    services.tools.protocols = services.protocols
    engine.executor._notify_receipt = notify
    invocation = asyncio.create_task(engine.invoke(parent_plan, parent, {"original": True}))
    fresh: RuntimeServices | None = None
    recovered_engine: Any = None
    try:
        async with asyncio.timeout(30):
            await entered.wait()
        binding = services.children.connection.execute(
            "SELECT child_id FROM child_workflow_bindings WHERE parent_id=?",
            (parent.invocation_id,),
        ).fetchone()
        assert binding is not None
        child_id = binding[0]
        assert isinstance(child_id, str)
        assert len(calls) == 1
        async with asyncio.timeout(30):
            await engine.shutdown()
        result = await asyncio.gather(invocation, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert not engine._active and not engine._ownership_tasks and not engine._execution_leases
        saved_parent, saved_child = (
            _record(services, parent.invocation_id),
            _record(services, child_id),
        )
        assert saved_parent.status == saved_child.status == "running"
        assert (
            services.invocations.read_input(saved_parent)
            == services.invocations.read_input(saved_child)
            == {"original": True}
        )
        assert not [
            event
            for event in services.lifecycle_events.snapshot()
            if event.data.get("event_type") in {"WorkflowCancelled", "TaskCancelled"}
        ]
        services.close()
        fresh = _services(tmp_path, backend)
        _plans(fresh)
        recovered_engine = engine_type()
        await recovered_engine.initialize(fresh)
        fresh.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(action)))
        fresh.tools.protocols = fresh.protocols
        saved_parent, saved_child = (
            _record(fresh, parent.invocation_id),
            _record(fresh, child_id),
        )
        if boundary == "known-receipt":
            assert call_identity is not None
            before = fresh.effect_journal.get(call_identity)
            assert before is not None and before.status == "completed"
            child_result = await recovered_engine.resume(
                saved_child, {"replacement": True}, child_plan
            )
            assert child_result.status == "completed" and child_result.output == {
                "accepted": "child"
            }
            parent_result = await recovered_engine.resume(
                saved_parent, {"replacement": True}, parent_plan
            )
            assert parent_result.status == "completed" and parent_result.output == {
                "accepted": "child"
            }
            assert fresh.effect_journal.get(call_identity) == before
        else:
            # Uncertain external/model/sandbox calls are not replayable merely
            # because a host drained. The receiver has no declared recovery proof.
            rejected = await recovered_engine.resume(saved_parent, None, parent_plan)
            assert rejected.status == "faulted" and rejected.error
            assert "recovery" in rejected.error["message"] or "intent" in rejected.error["message"]
            assert _record(fresh, child_id).status == "running"
        assert len(calls) == 1
        assert isinstance(fresh.model, FakeModel) and fresh.model.calls == []
    finally:
        release.set()
        if not invocation.done():
            invocation.cancel()
            await asyncio.gather(invocation, return_exceptions=True)
        if recovered_engine is not None:
            await recovered_engine.shutdown()
        if fresh is not None:
            fresh.close()
        else:
            await engine.shutdown()
            services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_lost_parent_owner_cannot_turn_interruption_into_child_cancellation(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    first = _services(tmp_path, backend)
    parent_plan, _ = _plans(first)
    engine = engine_type()
    await engine.initialize(first)
    parent = _parent(first, engine_name, parent_plan)
    entered, release, renew = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def action(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Idempotency-Key"])
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"late": True})

    async def renewal() -> None:
        await renew.wait()
        await asyncio.sleep(0.01)

    first.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(action)))
    first.tools.protocols = first.protocols
    engine._wait_for_ownership_renewal = renewal
    invocation = asyncio.create_task(engine.invoke(parent_plan, parent, {"original": True}))
    second = _services(tmp_path, backend)
    successor: Any = None
    try:
        async with asyncio.timeout(30):
            await entered.wait()
        child_id = first.children.connection.execute(
            "SELECT child_id FROM child_workflow_bindings WHERE parent_id=?",
            (parent.invocation_id,),
        ).fetchone()[0]
        original_lease = first.execution_ownership.renew(parent._execution_lease, seconds=0.05)
        second_parent = _record(second, parent.invocation_id)
        async with asyncio.timeout(5):
            while successor is None:
                try:
                    successor = second.execution_ownership.acquire(second_parent, seconds=300)
                except InvocationStateConflict:
                    await asyncio.sleep(0.01)
        assert (
            successor.generation == original_lease.generation + 1
            and successor.owner != original_lease.owner
        )
        renew.set()
        async with asyncio.timeout(30):
            result = await asyncio.gather(invocation, return_exceptions=True)
        assert isinstance(result[0], InvocationStateConflict)
        assert (
            _record(first, parent.invocation_id).status
            == _record(first, child_id).status
            == "running"
        )
        child_before = _record(second, child_id)._persisted_payload
        with pytest.raises(InvocationStateConflict):
            await engine._cancel_child_scopes(parent, ("/do",), (), reason="workflow_end")
        assert _record(second, child_id)._persisted_payload == child_before
        assert len(calls) == 1
    finally:
        release.set()
        await engine.shutdown()
        if successor is not None:
            second.execution_ownership.release(successor)
        second.close()
        first.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("reply", ["observed", "unobserved"])
async def test_other_worker_operator_cancel_remains_authoritative_for_active_nested_await(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    reply: str,
) -> None:
    first = _services(tmp_path, backend)
    second = _services(tmp_path, backend)
    parent_plan, _ = _plans(first)
    local, operator = engine_type(), engine_type()
    await local.initialize(first)
    await operator.initialize(second)
    parent = _parent(first, engine_name, parent_plan)
    entered, release, heartbeat, late = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    calls: list[str] = []
    remote_commits: list[str] = []

    async def action(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Idempotency-Key"])
        remote_commits.append(request.headers["Idempotency-Key"])
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # A remote action can finish after cancellation; model that late
            # reply explicitly rather than claiming cooperative hard preemption.
            late.set()
            await release.wait()
            if reply == "unobserved":
                raise InvocationCancelled("remote reply unavailable after cancellation") from None
        return httpx.Response(200, json={"late": True})

    async def renewal() -> None:
        await heartbeat.wait()
        await asyncio.sleep(0.01)

    first.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(action)))
    first.tools.protocols = first.protocols
    local._wait_for_ownership_renewal = renewal
    invocation = asyncio.create_task(local.invoke(parent_plan, parent, {"original": True}))
    try:
        async with asyncio.timeout(30):
            await entered.wait()
        child_id = first.children.connection.execute(
            "SELECT child_id FROM child_workflow_bindings WHERE parent_id=?",
            (parent.invocation_id,),
        ).fetchone()[0]
        authoritative = _record(second, parent.invocation_id)
        cancelled = await operator.cancel(authoritative, operation_id="operator-authority")
        assert cancelled.status == "cancelled"
        assert _record(second, child_id).status == "cancelled"
        parent_before = _record(second, parent.invocation_id)._persisted_payload
        child_before = _record(second, child_id)._persisted_payload
        heartbeat.set()
        async with asyncio.timeout(30):
            await late.wait()
        assert _record(second, parent.invocation_id)._persisted_payload == parent_before
        assert _record(second, child_id)._persisted_payload == child_before
        release.set()
        async with asyncio.timeout(30):
            result = await invocation
        assert result.status == "cancelled" and result.output is None
        release.set()
        assert _record(second, parent.invocation_id)._persisted_payload == parent_before
        assert _record(second, child_id)._persisted_payload == child_before
        duplicate = await operator.resume(authoritative, {"late": "replacement"}, parent_plan)
        assert duplicate.status == "cancelled" and duplicate.output is None
        assert len(calls) == 1
        assert remote_commits == calls
        rows = first.effect_journal.connection.execute(
            "SELECT identity,payload FROM workflow_effect_invocations WHERE invocation_id=?",
            (child_id,),
        ).fetchall()
        identities = [
            EffectIdentity.from_value(json.loads(row[1]))
            for row in rows
            if json.loads(row[1])["operation"] == "call:http"
        ]
        assert len(identities) == 1
        uncertain = first.effect_journal.get(identities[0])
        assert uncertain is not None
        if reply == "unobserved":
            assert uncertain.status == "pending"
            assert uncertain.output is None and uncertain.error is None
        else:
            assert uncertain.status == "completed"
            assert uncertain.output == {"late": True} and uncertain.error is None
        assert not local._active and not local._ownership_tasks
    finally:
        release.set()
        heartbeat.set()
        await local.shutdown()
        await operator.shutdown()
        if not invocation.done():
            invocation.cancel()
            await asyncio.gather(invocation, return_exceptions=True)
        second.close()
        first.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("control", ["deadline", "workflow_end"])
async def test_active_child_scope_control_preserves_committed_external_uncertainty(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    control: str,
) -> None:
    services = _services(tmp_path, backend)
    parent_plan, child_plan = _plans(services)
    child_task = parent_plan.source["do"][0]["child"]
    source = _source("positive-control", [{"child": child_task}])
    if control == "deadline":
        source["timeout"] = {"after": {"seconds": 5}}
    else:
        source["do"] = [
            {
                "parallel": {
                    "fork": {
                        "branches": [
                            {"child": child_task},
                            {
                                "finish": {
                                    "call": "http",
                                    "with": {
                                        "method": "POST",
                                        "endpoint": "https://control.test/end",
                                    },
                                    "then": "end",
                                }
                            },
                        ]
                    }
                }
            },
            {"unreachable": {"set": {"wrong": True}}},
        ]
    plan = compile_workflow(source)
    engine = engine_type()
    await engine.initialize(services)
    native = engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    if not native:
        await engine.shutdown()
        services.close()
        pytest.skip("actual independent native SDK environment required")
    entered, stopped = asyncio.Event(), asyncio.Event()
    commits: list[str] = []
    calls: list[str] = []

    async def action(request: httpx.Request) -> httpx.Response:
        phase = "child" if request.url.host == "child.test" else "end"
        calls.append(phase)
        if phase == "end":
            await entered.wait()
            return httpx.Response(200, json={"ended": True})
        commits.append(request.headers["Idempotency-Key"])
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        return httpx.Response(200, json={"unreachable": True})

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(action)))
    services.tools.protocols = services.protocols
    parent = _parent(services, engine_name, plan)
    invocation = asyncio.create_task(engine.invoke(plan, parent, {"original": True}))
    try:
        async with asyncio.timeout(30):
            await entered.wait()
        binding = services.children.connection.execute(
            "SELECT child_id FROM child_workflow_bindings WHERE parent_id=?",
            (parent.invocation_id,),
        ).fetchone()
        assert binding is not None
        child_id = binding[0]
        clock_before = services.effect_journal.connection.execute(
            "SELECT e.payload FROM workflow_effect_invocations i "
            "JOIN workflow_effects e ON e.identity=i.identity "
            "WHERE i.invocation_id=? AND i.payload LIKE ?",
            (parent.invocation_id, '%"policy:clock:v1"%'),
        ).fetchall()
        assert len(clock_before) == (1 if control == "deadline" else 0)
        async with asyncio.timeout(30):
            result = await invocation
        assert stopped.is_set() and len(commits) == 1
        child = _record(services, child_id)
        assert child.status == "cancelled" and child.output is None
        assert child.error and child.error["details"]["reason"] == control
        assert child.workflow_fingerprint == child_plan.fingerprint
        assert services.invocations.read_input(child) == {"original": True}
        rows = services.effect_journal.connection.execute(
            "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?",
            (child_id,),
        ).fetchall()
        identities = [
            EffectIdentity.from_value(json.loads(row[0]))
            for row in rows
            if json.loads(row[0])["operation"] == "call:http"
        ]
        assert len(identities) == 1
        uncertain = services.effect_journal.get(identities[0])
        assert uncertain is not None and uncertain.status == "pending"
        assert uncertain.operation_id == commits[0]
        assert uncertain.output is None and uncertain.error is None
        if control == "deadline":
            assert result.status == "faulted" and result.error
            assert "unresolved execution intent" in result.error["message"]
            parent_rows = services.effect_journal.connection.execute(
                "SELECT i.payload,e.payload FROM workflow_effect_invocations i "
                "JOIN workflow_effects e ON e.identity=i.identity WHERE i.invocation_id=?",
                (parent.invocation_id,),
            ).fetchall()
            deadlines = [
                json.loads(row[1])
                for row in parent_rows
                if json.loads(row[0])["operation"] == "policy:clock:v1"
            ]
            assert len(deadlines) == 1 and deadlines[0]["status"] == "completed"
            assert deadlines[0] == json.loads(clock_before[0][0])
            assert deadlines[0]["output"]["profile"] == "owa/timing/v1"
            assert engine.executor._absolute_clock() >= deadlines[0]["output"]["started_at"] + 5
            assert plan.source["timeout"] == {"after": {"seconds": 5}}
            assert calls == ["child"]
        else:
            assert result.status == "completed" and result.output == {"ended": True}
            assert sorted(calls) == ["child", "end"]
        before = child._persisted_payload
        duplicate = await engine.resume(parent, {"replacement": True}, plan)
        assert duplicate.as_dict() == result.as_dict()
        assert _record(services, child_id)._persisted_payload == before
        assert services.effect_journal.get(identities[0]) == uncertain
        assert len(commits) == 1
        assert isinstance(services.model, FakeModel) and services.model.calls == []
        assert not engine._active and not engine._ownership_tasks
    finally:
        if not invocation.done():
            invocation.cancel()
            await asyncio.gather(invocation, return_exceptions=True)
        await engine.shutdown()
        services.close()


def _start(directory: Path, mode: str) -> subprocess.Popen[bytes]:
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "HOME",
        "OWA_TEST_POSTGRES_URL",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    paths = [
        ROOT / "core/src",
        *(ROOT / "engines" / name / "src" for name in ("adk", "langgraph", "agent-framework")),
    ]
    paths.extend(Path(value) for value in sys.path if value and Path(value).is_dir())
    environment["PYTHONPATH"] = os.pathsep.join(str(path) for path in paths)
    environment["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), str(WORKER), str(directory), mode],
        cwd=directory,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("stop_mode", ["drain", "kill"])
def test_native_nested_worker_shutdown_or_process_stop_preserves_child_recovery(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    stop_mode: str,
) -> None:
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    native = engine_type().native
    if not (native.task_nodes_available if engine_name == "langgraph" else native.available):
        pytest.skip("actual independent native dependency environment is required")
    (tmp_path / "scenario.json").write_text(
        json.dumps(
            {
                "engine": engine_name,
                "backend": backend,
                "original": {"request": str(uuid4()), "original": "must-survive"},
            }
        ),
        encoding="utf-8",
    )
    first = _start(tmp_path, "start")
    try:
        deadline = time.monotonic() + 60
        while not (tmp_path / "ready.json").exists():
            failure = tmp_path / "start-failure.json"
            assert first.poll() is None, (
                failure.read_text() if failure.exists() else "producer exited"
            )
            assert time.monotonic() < deadline, "producer did not reach known child receipt"
            time.sleep(0.02)
        ready = json.loads((tmp_path / "ready.json").read_text())
        assert ready["pid"] == first.pid and ready["native_available"]
        if stop_mode == "kill":
            first.kill()
            assert first.wait(timeout=15) != 0
            assert not (tmp_path / "start-cleaned.json").exists()
        else:
            (tmp_path / "drain.request").write_text("drain", encoding="utf-8")
            assert first.wait(timeout=45) == 0
            drained = json.loads((tmp_path / "start-cleaned.json").read_text())
            assert drained["parent_status"] == drained["child_status"] == "running"
    finally:
        _stop(first)
    second = _start(tmp_path, "recover")
    try:
        failure = tmp_path / "recover-failure.json"
        assert second.wait(timeout=75) == 0, (
            failure.read_text() if failure.exists() else "recovery exited"
        )
        result = json.loads((tmp_path / "result.json").read_text())
        assert result["pid"] != ready["pid"]
        assert result["parent_id"] == ready["parent_id"] and result["child_id"] == ready["child_id"]
        assert result["parent_status"] == result["child_status"] == "completed"
        assert result["output"] == {"accepted": ready["original"]}
        assert result["original"] == result["child_input"] == ready["original"]
        assert result["fingerprint"] == ready["fingerprint"] and result["budget_used"] == 1
        assert result["model_calls"] == 0 and result["receipt_unchanged"]
        with closing(sqlite3.connect(tmp_path / "external.sqlite3")) as external:
            calls = external.execute("SELECT operation_id,body FROM calls").fetchall()
        assert len(calls) == len({row[0] for row in calls}) == 1
        assert json.loads(calls[0][1]) == ready["original"]
    finally:
        _stop(second)
