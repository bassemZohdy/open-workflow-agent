"""Parent-owned nonresident child waits preserve native recovery and cancellation."""

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

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.forks import ForkIdentity
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import WorkflowExecutor, compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


def _source(name: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "document": {"dsl": "1.0.3", "namespace": "child-waits", "name": name, "version": "1.0.0"},
        "do": tasks,
    }


def _run(name: str, *, detached: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {
        "workflow": {
            "namespace": "child-waits",
            "name": name,
            "version": "1.0.0",
        }
    }
    if detached:
        value["await"] = False
    return {"run": value}


def _services(path: Path, backend: str, model: FakeModel) -> RuntimeServices:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    return RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=model,
        database_root=None if datasource else path,
    )


def _clock(monkeypatch: pytest.MonkeyPatch, current: list[float]) -> None:
    original = WorkflowExecutor.__init__

    def initialize(executor: WorkflowExecutor, *args: Any, **kwargs: Any) -> None:
        original(executor, *args, **kwargs)
        executor._absolute_clock = lambda: current[0]

    monkeypatch.setattr(WorkflowExecutor, "__init__", initialize)


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


def _idle(engine: Any, *handles: Any) -> None:
    for handle in handles:
        assert handle.invocation_id not in engine._active
        assert handle.invocation_id not in engine._ownership_tasks
        assert handle._execution_lease is None


async def _drain(dispatcher: Any) -> None:
    async with asyncio.timeout(30):
        while dispatcher._workers:
            await asyncio.sleep(0.01)
    assert not dispatcher._errors, tuple(str(error) for error in dispatcher._errors)


async def _advance_child(engine: Any, child_id: str, now: float) -> None:
    dispatcher = engine._timer_dispatcher
    assert await dispatcher.tick(now=now, invocation_id=child_id) == 1
    await _drain(dispatcher)


async def _advance_parent(engine: Any, run_key: str) -> None:
    dispatcher = engine._timer_dispatcher.child_waits
    await dispatcher.tick(run_key=run_key)
    await _drain(dispatcher)


async def _null_response(_prompt: Any) -> None:
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("reopen", [False, True])
@pytest.mark.parametrize("outcome", ["value", "null", "fault"])
async def test_parent_releases_execution_and_receives_child_terminal_outcome_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    reopen: bool,
    outcome: str,
) -> None:
    current = [1000.0]
    _clock(monkeypatch, current)
    expected = None if outcome == "null" else {"child_result": "actual"}
    model = FakeModel(_null_response if outcome == "null" else expected)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    tail = (
        {
            "raise": {
                "error": {
                    "type": "https://child.test/fault",
                    "status": 422,
                    "title": "child failed",
                }
            }
        }
        if outcome == "fault"
        else {"call": "llm:1.0.0@default", "with": {"prompt": "child-after"}}
    )
    child_source = _source("child", [{"pause": {"wait": {"seconds": 60}}}, {"finish": tail}])
    parent_source = _source("parent", [{"start": _run("child")}])
    plan = compile_workflow(parent_source)
    original = {"original": "retained"}
    try:
        services.workflow_catalog.register(child_source)
        await engine.initialize(services)
        parent = _parent(services, engine_name, plan)
        result = await asyncio.wait_for(engine.invoke(plan, parent, original), timeout=20)
        assert result.status == "waiting", result.error
        waits = services.child_waits.for_invocation(parent.invocation_id, plan.fingerprint)
        assert len(waits) == 1
        registration = waits[0]
        child = services.invocations.get(registration.suspension.child_id)
        assert child is not None and child.status == "waiting"
        assert child.parent_invocation_id == parent.invocation_id
        assert child.parent_task_reference == "/do/0/start"
        assert child.owner_principal == parent.owner_principal == "principal"
        assert child.user_id == parent.user_id == "correlation"
        assert child.session_id != parent.session_id
        assert services.invocations.read_input(child) == original
        _idle(engine, parent, child)
        timer = services.timers.pending_for_invocation(
            child.invocation_id, child.workflow_fingerprint
        )[0]
        assert timer.armed_at == 1000 and timer.due_at == 1060
        admission = registration.receipt
        child_id, parent_id = child.invocation_id, parent.invocation_id
        if reopen:
            await engine.shutdown()
            services.close()
            services = _services(tmp_path, backend, model)
            services.workflow_catalog.register(child_source)
            engine = engine_type()
            await engine.initialize(services)
            parent = services.invocations.get(parent_id)
            child = services.invocations.get(child_id)
            assert parent is not None and child is not None
        before = parent._persisted_payload, child._persisted_payload
        current[0] = 1020
        early = await engine.resume(parent, {"untrusted": "must-not-replace-input"}, plan)
        assert early.status == "waiting" and not model.calls
        assert services.invocations.get(parent_id)._persisted_payload == before[0]
        assert services.invocations.get(child_id)._persisted_payload == before[1]
        assert services.timers.registration_for_key(timer.timer_key).due_at == 1060
        assert services.child_waits.registration(registration.identity).receipt == admission
        _idle(engine, parent, child)
        current[0] = 1060
        await _advance_child(engine, child_id, current[0])
        terminal_child = services.invocations.get(child_id)
        assert terminal_child.status == ("faulted" if outcome == "fault" else "completed")
        await _advance_parent(engine, registration.identity.key)
        terminal_parent = services.invocations.get(parent_id)
        assert terminal_parent.status == terminal_child.status
        if outcome == "fault":
            assert terminal_parent.error["details"]["invocation_id"] == child_id
            assert not model.calls
        else:
            assert terminal_parent.output == terminal_child.output == expected
            assert model.calls == [{"prompt": "child-after"}]
        wake = services.child_waits.known_wakes(parent_id, plan.fingerprint)
        assert wake[registration.identity.key]["reason"] == "child_terminal"
        assert services.invocations.read_input(terminal_parent) == original
        duplicate = await engine.resume(terminal_parent, {"duplicate": True}, plan)
        assert (
            duplicate.status == terminal_parent.status
            and duplicate.output == terminal_parent.output
        )
        await _advance_parent(engine, registration.identity.key)
        assert len(model.calls) == (0 if outcome == "fault" else 1)
        assert (
            services.children.connection.execute(
                "SELECT COUNT(*) FROM child_workflow_bindings WHERE root_id=?", (parent_id,)
            ).fetchone()[0]
            == 1
        )
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_nested_child_waits_unwind_two_native_levels_without_resident_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    current = [1000.0]
    _clock(monkeypatch, current)
    model = FakeModel({"leaf": True})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    leaf = services.workflow_catalog.register(
        _source(
            "leaf",
            [
                {"pause": {"wait": {"seconds": 60}}},
                {"finish": {"call": "llm:1.0.0@default", "with": {"prompt": "leaf"}}},
            ],
        )
    )
    middle = services.workflow_catalog.register(_source("middle", [{"start": _run("leaf")}]))
    plan = compile_workflow(_source("root", [{"start": _run("middle")}]))
    try:
        await engine.initialize(services)
        root = _parent(services, engine_name, plan)
        waiting = await asyncio.wait_for(engine.invoke(plan, root, {"original": True}), timeout=20)
        assert waiting.status == "waiting", waiting.error
        outer = services.child_waits.for_invocation(root.invocation_id, plan.fingerprint)[0]
        child = services.invocations.get(outer.suspension.child_id)
        inner = services.child_waits.for_invocation(child.invocation_id, middle.fingerprint)[0]
        grandchild = services.invocations.get(inner.suspension.child_id)
        assert grandchild.workflow_fingerprint == leaf.fingerprint
        assert len({root.invocation_id, child.invocation_id, grandchild.invocation_id}) == 3
        assert len({root.session_id, child.session_id, grandchild.session_id}) == 3
        assert all(handle.status == "waiting" for handle in (root, child, grandchild))
        _idle(engine, root, child, grandchild)
        current[0] = 1060
        await _advance_child(engine, grandchild.invocation_id, current[0])
        await _advance_parent(engine, inner.identity.key)
        await _advance_parent(engine, outer.identity.key)
        for handle in (root, child, grandchild):
            saved = services.invocations.get(handle.invocation_id)
            assert saved.status == "completed" and saved.output == {"leaf": True}
            assert services.invocations.read_input(saved) == {"original": True}
        assert model.calls == [{"prompt": "leaf"}]
        rows = services.children.connection.execute(
            "SELECT depth FROM child_workflow_bindings WHERE root_id=? ORDER BY depth",
            (root.invocation_id,),
        ).fetchall()
        assert [row[0] for row in rows] == [1, 2]
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("scope", ["task", "workflow"])
async def test_parent_original_deadline_cancels_later_child_timer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    scope: str,
) -> None:
    current = [1000.0]
    _clock(monkeypatch, current)
    model = FakeModel({"must": "not-dispatch"})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    services.workflow_catalog.register(
        _source(
            "child",
            [{"pause": {"wait": {"seconds": 60}}}, {"after": {"call": "llm:1.0.0@default"}}],
        )
    )
    task = _run("child")
    source = _source("deadline-parent", [{"start": task}])
    (task if scope == "task" else source)["timeout"] = {"after": {"seconds": 30}}
    plan = compile_workflow(source)
    try:
        await engine.initialize(services)
        parent = _parent(services, engine_name, plan)
        result = await engine.invoke(plan, parent, {"original": True})
        assert result.status == "waiting", result.error
        registration = services.child_waits.for_invocation(parent.invocation_id, plan.fingerprint)[
            0
        ]
        assert registration.suspension.deadline_at == 1030
        child = services.invocations.get(registration.suspension.child_id)
        timer = services.timers.pending_for_invocation(
            child.invocation_id, child.workflow_fingerprint
        )[0]
        assert timer.due_at == 1060
        current[0] = 1031
        await _advance_parent(engine, registration.identity.key)
        parent = services.invocations.get(parent.invocation_id)
        assert parent.status == "faulted", parent.error
        assert parent.error["details"]["status"] == 408
        assert (
            parent.error["details"]["type"]
            == "https://serverlessworkflow.io/spec/1.0.0/errors/timeout"
        )
        assert services.invocations.get(child.invocation_id).status == "cancelled"
        assert services.timers.registration_for_key(timer.timer_key).due_at == 1060
        assert (
            services.child_waits.registration(registration.identity).suspension.deadline_at == 1030
        )
        assert (
            services.child_waits.known_wakes(parent.invocation_id, plan.fingerprint)[
                registration.identity.key
            ]["reason"]
            == "deadline"
        )
        current[0] = 1060
        assert (
            await engine._timer_dispatcher.tick(now=current[0], invocation_id=child.invocation_id)
            == 0
        )
        assert not model.calls
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("reopen", [False, True])
async def test_durable_parent_cancel_cascades_only_synchronous_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    reopen: bool,
) -> None:
    current = [1000.0]
    _clock(monkeypatch, current)
    model = FakeModel({"independent": True})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    synchronous = _source(
        "synchronous",
        [
            {"pause": {"wait": {"seconds": 60}}},
            {"after": {"call": "llm:1.0.0@default", "with": {"prompt": "synchronous"}}},
        ],
    )
    detached = _source(
        "detached",
        [
            {"pause": {"wait": {"seconds": 600}}},
            {"after": {"call": "llm:1.0.0@default", "with": {"prompt": "detached"}}},
        ],
    )
    plan = compile_workflow(
        _source(
            "cancel-parent",
            [{"independent": _run("detached", detached=True)}, {"owned": _run("synchronous")}],
        )
    )
    try:
        services.workflow_catalog.register(synchronous)
        services.workflow_catalog.register(detached)
        await engine.initialize(services)
        parent = _parent(services, engine_name, plan)
        result = await engine.invoke(plan, parent, {"original": True})
        assert result.status == "waiting", result.error
        registration = services.child_waits.for_invocation(parent.invocation_id, plan.fingerprint)[
            0
        ]
        owned_id = registration.suspension.child_id
        jobs = [
            job
            for job in services.children.pending_jobs(engine_name)
            if job.invocation_id == parent.invocation_id
        ]
        assert len(jobs) == 1
        decision = jobs[0]
        detached_id = services.effect_journal.get(decision).output["child_id"]
        parent_id = parent.invocation_id
        if reopen:
            await engine.shutdown()
            services.close()
            services = _services(tmp_path, backend, model)
            services.workflow_catalog.register(synchronous)
            services.workflow_catalog.register(detached)
            engine = engine_type()
            await engine.initialize(services)
            parent = services.invocations.get(parent_id)
        cancelled = await engine.cancel(parent, operation_id="durable-parent-cancel")
        assert cancelled.status == "cancelled"
        assert services.invocations.get(owned_id).status == "cancelled"
        assert services.invocations.get(detached_id).status == "running"
        dispatcher = engine._timer_dispatcher.children
        await dispatcher.tick(job_key=decision.key)
        await _drain(dispatcher)
        independent = services.invocations.get(detached_id)
        assert independent.status == "waiting"
        current[0] = 1600
        assert await engine._timer_dispatcher.tick(now=current[0], invocation_id=owned_id) == 0
        await _advance_child(engine, detached_id, current[0])
        assert services.invocations.get(detached_id).status == "completed"
        assert model.calls == [{"prompt": "detached"}]
        assert services.invocations.get(parent_id).status == "cancelled"
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "corruption",
    [
        "missing-wait",
        "changed-deadline",
        "changed-binding",
        "changed-child-input",
        "changed-child-owner",
        "missing-child-arm",
    ],
)
async def test_corrupt_saved_child_wait_rejects_before_parent_or_child_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    corruption: str,
) -> None:
    current = [1000.0]
    _clock(monkeypatch, current)
    model = FakeModel({"must": "not-dispatch"})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    services.workflow_catalog.register(
        _source(
            "child",
            [{"pause": {"wait": {"seconds": 60}}}, {"after": {"call": "llm:1.0.0@default"}}],
        )
    )
    plan = compile_workflow(_source("corrupt-parent", [{"start": _run("child")}]))
    parent = child = None
    try:
        await engine.initialize(services)
        parent = _parent(services, engine_name, plan)
        result = await engine.invoke(plan, parent, {"original": True})
        assert result.status == "waiting", result.error
        registration = services.child_waits.for_invocation(parent.invocation_id, plan.fingerprint)[
            0
        ]
        child = services.invocations.get(registration.suspension.child_id)
        original = parent._persisted_payload, child._persisted_payload
        connection = services.effect_journal.connection
        if corruption == "missing-wait":
            connection.execute(
                "DELETE FROM child_workflow_waits WHERE run_key=?", (registration.identity.key,)
            )
        elif corruption == "changed-deadline":
            connection.execute(
                "UPDATE child_workflow_waits SET deadline_at=? WHERE run_key=?",
                (1099, registration.identity.key),
            )
        elif corruption == "changed-binding":
            connection.execute(
                "UPDATE child_workflow_bindings SET request_digest=? WHERE binding_key=?",
                ("f" * 64, registration.identity.key),
            )
        elif corruption == "missing-child-arm":
            timer = services.timers.pending_for_invocation(
                child.invocation_id, child.workflow_fingerprint
            )[0]
            connection.execute("DELETE FROM workflow_effects WHERE identity=?", (timer.timer_key,))
        else:
            # Alter actual common evidence through its persistence path, then
            # compare against that corrupted record to detect resume mutation.
            if corruption == "changed-child-owner":
                payload = json.loads(child._persisted_payload)
                payload["owner_principal"] = "another-principal"
                services.invocations.connection.execute(
                    "UPDATE invocations SET payload=? WHERE invocation_id=?",
                    (json.dumps(payload), child.invocation_id),
                )
                services.invocations.connection.commit()
            else:
                input_row = services.invocations.connection.execute(
                    "SELECT payload FROM invocation_inputs WHERE invocation_id=?",
                    (child.invocation_id,),
                ).fetchone()
                assert input_row is not None
                services.invocations.connection.execute(
                    "UPDATE invocation_inputs SET payload=? WHERE invocation_id=?",
                    (json.dumps({"changed": True}), child.invocation_id),
                )
                services.invocations.connection.commit()
        connection.commit()
        if corruption == "missing-child-arm":
            current[0] = 1060
            await engine._timer_dispatcher.tick(now=current[0], invocation_id=child.invocation_id)
            async with asyncio.timeout(30):
                while engine._timer_dispatcher._workers:
                    await asyncio.sleep(0.01)
            assert engine._timer_dispatcher._errors
            assert services.invocations.get(child.invocation_id)._persisted_payload == original[1]
            assert services.invocations.get(parent.invocation_id)._persisted_payload == original[0]
        else:
            before = (
                services.invocations.get(parent.invocation_id)._persisted_payload,
                services.invocations.get(child.invocation_id)._persisted_payload,
            )
            with pytest.raises(InvocationStateError):
                await engine.resume(parent, {"changed": "input"}, plan)
            assert services.invocations.get(parent.invocation_id)._persisted_payload == before[0]
            assert services.invocations.get(child.invocation_id)._persisted_payload == before[1]
        assert not model.calls
        _idle(engine, parent, child)
    finally:
        # Corrupted evidence remains in its original rows. Terminal control
        # cleanup prevents another shared-database fixture from delivering it.
        for handle in (parent, child):
            if handle is not None:
                saved = services.invocations.get(handle.invocation_id)
                if saved is not None and saved.status in {"running", "waiting"}:
                    services.invocations.update(saved, status="cancelled")
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_corrupt_child_native_checkpoint_cannot_advance_common_waiting_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    native = engine_type().native
    if not (native.task_nodes_available if engine_name == "langgraph" else native.available):
        pytest.skip("independent native engine environment is required")
    current = [1000.0]
    _clock(monkeypatch, current)
    model = FakeModel({"must": "not-dispatch"})
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    child_plan = services.workflow_catalog.register(
        _source(
            "native-child",
            [{"pause": {"wait": {"seconds": 60}}}, {"after": {"call": "llm:1.0.0@default"}}],
        )
    )
    plan = compile_workflow(_source("native-parent", [{"start": _run("native-child")}]))
    parent = child = None
    try:
        await engine.initialize(services)
        parent = _parent(services, engine_name, plan)
        result = await engine.invoke(plan, parent, {"original": True})
        assert result.status == "waiting", result.error
        registration = services.child_waits.for_invocation(parent.invocation_id, plan.fingerprint)[
            0
        ]
        child = services.invocations.get(registration.suspension.child_id)
        before = parent._persisted_payload, child._persisted_payload
        if engine_name == "langgraph":
            from open_workflow_agent.workflow import ExecutionState
            from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

            async def corrupt(saver: Any) -> None:
                driver = LangGraphScopeExecutor(child_plan, saver, child.invocation_id)
                graph = driver.compile_scope(
                    child_plan.scopes[0], engine.executor, ExecutionState(data={})
                )
                await graph.aupdate_state(driver.checkpoint_config("/do", 1), {"snapshot": "{}"})

            await engine.native._with_saver(corrupt)
        else:
            from google.adk.events import Event
            from open_workflow_agent_adk.native import _RECEIPT_AUTHOR

            sessions = engine.native._session_service(engine._session_database_path())
            try:
                session = await sessions.get_session(
                    app_name="open-workflow-agent",
                    user_id=child.user_id or "owa-user",
                    session_id=child.session_id,
                )
                assert session is not None
                await sessions.append_event(
                    session=session,
                    event=Event(
                        invocation_id=child.engine_execution_reference,
                        author=_RECEIPT_AUTHOR,
                        custom_metadata={"sequence": 999},
                    ),
                )
            finally:
                await engine.native._close_sessions(sessions)
        current[0] = 1060
        await engine._timer_dispatcher.tick(now=current[0], invocation_id=child.invocation_id)
        async with asyncio.timeout(30):
            while engine._timer_dispatcher._workers:
                await asyncio.sleep(0.01)
        assert engine._timer_dispatcher._errors
        assert services.invocations.get(parent.invocation_id)._persisted_payload == before[0]
        assert services.invocations.get(child.invocation_id)._persisted_payload == before[1]
        assert not model.calls
        _idle(engine, parent, child)
    finally:
        for handle in (parent, child):
            if handle is not None:
                saved = services.invocations.get(handle.invocation_id)
                if saved is not None and saved.status in {"running", "waiting"}:
                    services.invocations.update(saved, status="cancelled")
        await engine.shutdown()
        services.close()


def _start_worker(directory: Path, mode: str) -> subprocess.Popen[bytes]:
    root = Path(__file__).resolve().parents[2]
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
    paths = [root / "core/src", *(root / "engines" / name / "src" for name in ("adk", "langgraph"))]
    paths.extend(Path(value) for value in sys.path if value and Path(value).is_dir())
    environment["PYTHONPATH"] = os.pathsep.join(str(path) for path in paths)
    environment["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        [
            getattr(sys, "_base_executable", sys.executable),
            str(Path(__file__).with_name("_child_wait_recovery_worker.py")),
            str(directory),
            mode,
        ],
        cwd=directory,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_mutual_recursion_faults_at_original_root_depth_before_extra_child(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str
) -> None:
    model = FakeModel({"must": "not-dispatch"})
    services = _services(tmp_path, backend, model)
    services.config.child_workflows.max_depth = 4
    services.config.child_workflows.max_children = 10
    engine = engine_type()
    first = services.workflow_catalog.register(_source("first", [{"recurse": _run("second")}]))
    second = services.workflow_catalog.register(_source("second", [{"recurse": _run("first")}]))
    try:
        await engine.initialize(services)
        root = _parent(services, engine_name, first)
        result = await asyncio.wait_for(engine.invoke(first, root, {"original": True}), timeout=30)
        assert result.status == "faulted", result.error
        rows = services.children.connection.execute(
            "SELECT child_id,parent_id,depth,parent_task_reference FROM child_workflow_bindings "
            "WHERE root_id=? ORDER BY depth",
            (root.invocation_id,),
        ).fetchall()
        assert len(rows) == 4 and [row[2] for row in rows] == [1, 2, 3, 4]
        records = [services.invocations.get(root.invocation_id)]
        for child_id, parent_id, depth, reference in rows:
            assert parent_id == records[-1].invocation_id
            assert reference == "/do/0/recurse"
            child = services.invocations.get(child_id)
            expected = second if depth % 2 else first
            assert child.workflow_fingerprint == expected.fingerprint
            assert child.workflow_name == expected.name and child.workflow_version == "1.0.0"
            records.append(child)
        assert len({record.invocation_id for record in records}) == 5
        assert len({record.session_id for record in records}) == 5
        for record in records:
            assert record.status == "faulted" and record.owner_principal == "principal"
            assert record.user_id == "correlation"
            assert services.invocations.read_input(record) == {"original": True}
        assert records[-1].error["message"] == "child workflow execution budget exhausted"
        assert services.children.connection.execute(
            "SELECT max_depth,max_children,used FROM child_workflow_budgets WHERE root_id=?",
            (root.invocation_id,),
        ).fetchone() == (4, 10, 4)
        assert not model.calls
        _idle(engine, *records)
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_competitive_fork_winner_cancels_already_suspended_child_loser(
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
            "child",
            [{"pause": {"wait": {"seconds": 60}}}, {"after": {"call": "llm:1.0.0@default"}}],
        )
    )
    plan = compile_workflow(
        _source(
            "fork-parent",
            [
                {
                    "race": {
                        "fork": {
                            "compete": True,
                            "branches": [
                                {"slow": _run("child")},
                                {"fast": {"set": {"winner": "fast"}}},
                            ],
                        }
                    }
                }
            ],
        )
    )
    try:
        await engine.initialize(services)
        parent = _parent(services, engine_name, plan)
        original = engine.executor._run_task

        async def wait_for_child(name: str, definition: Any, state: Any) -> Any:
            if name == "fast":
                async with asyncio.timeout(20):
                    while not services.child_waits.for_invocation(
                        parent.invocation_id, plan.fingerprint
                    ):
                        await asyncio.sleep(0.01)
            return await original(name, definition, state)

        monkeypatch.setattr(engine.executor, "_run_task", wait_for_child)
        result = await asyncio.wait_for(engine.invoke(plan, parent, {"original": True}), timeout=30)
        assert result.status == "completed" and result.output == {"winner": "fast"}, result.error
        waits = services.child_waits.for_invocation(
            parent.invocation_id, plan.fingerprint, pending_only=False
        )
        assert len(waits) == 1
        child = services.invocations.get(waits[0].suspension.child_id)
        assert child.status == "cancelled"
        assert child.parent_task_reference == "/do/0/race/fork/branches/0/slow"
        assert not services.timers.pending_for_invocation(
            child.invocation_id, child.workflow_fingerprint
        )
        current[0] = 1060
        assert (
            await engine._timer_dispatcher.tick(now=current[0], invocation_id=child.invocation_id)
            == 0
        )
        assert not model.calls
        identity = ForkIdentity(
            parent.invocation_id,
            plan.fingerprint,
            "/do/0/race",
            (("/do/0/race", 1), ("/do/0/race/@attempt", 1)),
        )
        decision = services.fork_decisions.get(identity)
        assert decision is not None and decision.drained and decision.winner == 1
        _idle(engine, parent, child)
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("compete", [False, True])
async def test_second_child_wake_reenters_fork_without_acknowledging_first_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    compete: bool,
) -> None:
    current = [1000.0]
    _clock(monkeypatch, current)
    model = FakeModel(lambda payload: payload)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    for name, seconds in (("first", 120), ("second", 60)):
        services.workflow_catalog.register(
            _source(
                name,
                [
                    {"pause": {"wait": {"seconds": seconds}}},
                    {"finish": {"call": "llm:1.0.0@default", "with": {"child": name}}},
                ],
            )
        )
    plan = compile_workflow(
        _source(
            "two-waiting-children",
            [
                {
                    "parallel": {
                        "fork": {
                            "compete": compete,
                            "branches": [{"first": _run("first")}, {"second": _run("second")}],
                        }
                    }
                }
            ],
        )
    )
    try:
        await engine.initialize(services)
        parent = _parent(services, engine_name, plan)
        result = await engine.invoke(plan, parent, {"original": True})
        assert result.status == "waiting", result.error
        registrations = services.child_waits.for_invocation(parent.invocation_id, plan.fingerprint)
        assert len(registrations) == 2
        by_name = {item.identity.task_reference.rsplit("/", 1)[1]: item for item in registrations}
        first_wait, second_wait = by_name["first"], by_name["second"]
        first = services.invocations.get(first_wait.suspension.child_id)
        second = services.invocations.get(second_wait.suspension.child_id)
        first_timer = services.timers.pending_for_invocation(
            first.invocation_id, first.workflow_fingerprint
        )[0]
        second_timer = services.timers.pending_for_invocation(
            second.invocation_id, second.workflow_fingerprint
        )[0]
        assert first_timer.due_at == 1120 and second_timer.due_at == 1060
        _idle(engine, parent, first, second)
        current[0] = 1020
        assert (await engine.resume(parent, {"early": True}, plan)).status == "waiting"
        assert not model.calls
        current[0] = 1060
        await _advance_child(engine, second.invocation_id, current[0])
        await _advance_parent(engine, second_wait.identity.key)
        saved_parent = services.invocations.get(parent.invocation_id)
        assert model.calls == [{"child": "second"}]
        assert services.timers.registration_for_key(first_timer.timer_key).due_at == 1120
        if compete:
            assert saved_parent.status == "completed" and saved_parent.output == {"child": "second"}
            assert services.invocations.get(first.invocation_id).status == "cancelled"
        else:
            assert saved_parent.status == "waiting", saved_parent.error
            assert services.invocations.get(first.invocation_id).status == "waiting"
            assert services.invocations.get(second.invocation_id).status == "completed"
            services.effect_journal.verify_invocation(parent.invocation_id, plan.fingerprint)
            services.child_waits.verify_invocation(parent.invocation_id, plan.fingerprint)
            services.timers.verify_invocation(parent.invocation_id, plan.fingerprint)
            if services.event_store is not None:
                services.event_store.verify_invocation(
                    parent.invocation_id, plan.fingerprint, require_profile=plan.uses_durable_events
                )
            current[0] = 1061
            assert (await engine.resume(saved_parent, {"early": "again"}, plan)).status == "waiting"
            assert model.calls == [{"child": "second"}]
            current[0] = 1120
            await _advance_child(engine, first.invocation_id, current[0])
            await _advance_parent(engine, first_wait.identity.key)
            saved_parent = services.invocations.get(parent.invocation_id)
            assert saved_parent.status == "completed", saved_parent.error
            assert saved_parent.output == [{"child": "first"}, {"child": "second"}]
            assert model.calls == [{"child": "second"}, {"child": "first"}]
        assert (
            services.children.connection.execute(
                "SELECT COUNT(*) FROM child_workflow_bindings WHERE root_id=?",
                (parent.invocation_id,),
            ).fetchone()[0]
            == 2
        )
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_outer_fork_sibling_wake_cancels_nested_suspended_child_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    current = [1000.0]
    _clock(monkeypatch, current)
    model = FakeModel(lambda payload: payload)
    services = _services(tmp_path, backend, model)
    engine = engine_type()
    for name, seconds in (("first", 120), ("nested", 180), ("second", 60)):
        services.workflow_catalog.register(
            _source(
                name,
                [
                    {"pause": {"wait": {"seconds": seconds}}},
                    {"after": {"call": "llm:1.0.0@default", "with": {"child": name}}},
                ],
            )
        )
    plan = compile_workflow(
        _source(
            "outer-fork-parent",
            [
                {
                    "outer": {
                        "fork": {
                            "compete": True,
                            "branches": [
                                {
                                    "inner": {
                                        "fork": {
                                            "branches": [
                                                {"first": _run("first")},
                                                {"nested": _run("nested")},
                                            ]
                                        }
                                    }
                                },
                                {"second": _run("second")},
                            ],
                        }
                    }
                }
            ],
        )
    )
    try:
        await engine.initialize(services)
        parent = _parent(services, engine_name, plan)
        result = await engine.invoke(plan, parent, {"original": True})
        assert result.status == "waiting", result.error
        waits = services.child_waits.for_invocation(parent.invocation_id, plan.fingerprint)
        assert len(waits) == 3
        by_name = {item.identity.task_reference.rsplit("/", 1)[1]: item for item in waits}
        current[0] = 1060
        winner = services.invocations.get(by_name["second"].suspension.child_id)
        await _advance_child(engine, winner.invocation_id, current[0])
        await _advance_parent(engine, by_name["second"].identity.key)
        parent = services.invocations.get(parent.invocation_id)
        assert parent.status == "completed" and parent.output == {"child": "second"}, parent.error
        assert model.calls == [{"child": "second"}]
        for name in ("first", "nested"):
            child = services.invocations.get(by_name[name].suspension.child_id)
            assert child.status == "cancelled"
            assert not services.timers.pending_for_invocation(
                child.invocation_id, child.workflow_fingerprint
            )
        current[0] = 1200
        for registration in waits:
            assert (
                await engine._timer_dispatcher.tick(
                    now=current[0], invocation_id=registration.suspension.child_id
                )
                == 0
            )
        assert model.calls == [{"child": "second"}]
    finally:
        await engine.shutdown()
        services.close()


def _worker_failure(directory: Path, mode: str) -> str:
    path = directory / (mode + "-failure.json")
    return path.read_text() if path.exists() else "worker exited without result"


@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "boundary",
    [
        "parent-waiting",
        "child-terminal",
        "parent-receipt",
        "parent-cancel-control",
        "parent-task-deadline",
        "parent-workflow-deadline",
    ],
)
def test_child_wait_survives_actual_native_producer_process_stop(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str, boundary: str
) -> None:
    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    native = engine_type().native
    if not (native.task_nodes_available if engine_name == "langgraph" else native.available):
        pytest.skip("independent native engine environment is required")
    (tmp_path / "scenario.json").write_text(
        json.dumps(
            {
                "engine": engine_name,
                "backend": backend,
                "boundary": boundary,
                "subject": str(uuid4()),
            }
        ),
        encoding="utf-8",
    )
    first = _start_worker(tmp_path, "start")
    try:
        deadline = time.monotonic() + 60
        while not (tmp_path / "ready.json").exists():
            assert first.poll() is None, _worker_failure(tmp_path, "start")
            assert time.monotonic() < deadline, "producer never reached committed child boundary"
            time.sleep(0.02)
        ready = json.loads((tmp_path / "ready.json").read_text())
        assert ready["pid"] == first.pid and first.poll() is None and ready["native_available"]
        assert ready["due_at"] == 1060
        deadline_boundary = boundary in {"parent-task-deadline", "parent-workflow-deadline"}
        if boundary == "parent-waiting" or deadline_boundary:
            assert ready["nonresident"]
        if deadline_boundary:
            assert ready["deadline_at"] == 1030
            assert ready["budget"][:3] == [32, 1000, 1]
        first.kill()
        assert first.wait(timeout=15) != 0
        assert not (tmp_path / "start-cleaned.json").exists()
    finally:
        if first.poll() is None:
            first.kill()
        first.wait(timeout=15)
    second = _start_worker(tmp_path, "recover")
    try:
        assert second.wait(timeout=90) == 0, _worker_failure(tmp_path, "recover")
        evidence = json.loads((tmp_path / "result.json").read_text())
        assert evidence["pid"] != first.pid
        assert evidence["parent_id"] == ready["parent_id"]
        assert evidence["child_id"] == ready["child_id"]
        assert evidence["run_key"] == ready["run_key"]
        assert evidence["timer_key"] == ready["timer_key"]
        assert evidence["due_at"] == ready["due_at"] == 1060
        expected_status = (
            "cancelled"
            if boundary == "parent-cancel-control"
            else "faulted"
            if deadline_boundary
            else "completed"
        )
        expected_output = (
            None if boundary == "parent-cancel-control" or deadline_boundary else {"accepted": True}
        )
        assert evidence["status"] == evidence["duplicate"]["status"] == expected_status
        assert evidence["output"] == evidence["duplicate"]["output"] == expected_output
        assert evidence["original"] == ready["original"]
        assert evidence["bindings"] == 1
        assert evidence["budget"] == ready["budget"]
        if deadline_boundary:
            assert evidence["deadline_at"] == ready["deadline_at"] == 1030
            assert evidence["child_status"] == "cancelled"
            assert evidence["error"]["details"]["status"] == 408
            assert (
                evidence["error"]["details"]["type"]
                == "https://serverlessworkflow.io/spec/1.0.0/errors/timeout"
            )
            assert evidence["duplicate"]["error"] == evidence["error"]
        with closing(sqlite3.connect(tmp_path / "external.sqlite3")) as external:
            calls = external.execute(
                "SELECT operation_id,body FROM calls ORDER BY rowid"
            ).fetchall()
        phases = (
            []
            if deadline_boundary
            else ["parent-before"]
            if boundary == "parent-cancel-control"
            else ["parent-before", "child", "parent-after"]
        )
        assert len(calls) == len({row[0] for row in calls}) == len(phases)
        assert [json.loads(row[1])["phase"] for row in calls] == phases
        assert all(json.loads(row[1])["original"] == ready["original"] for row in calls)
    finally:
        if second.poll() is None:
            second.kill()
        second.wait(timeout=15)
