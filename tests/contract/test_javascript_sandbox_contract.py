"""Shared actual-native JavaScript return and interpreter recovery fixtures."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from _grpc_recovery_worker import native_snapshot
from engine_cases import engine_cases
from javascript_fixture_policy import javascript_path
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.sandbox import compile_sandbox_workflow
from open_workflow_agent.services import RuntimeServices
from sandbox_fixture_policy import supported_process_limits
from test_process_crash_recovery import _environment
from test_workflow_version_recovery import _common_snapshot

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")
ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]


def actual_native(engine, name):
    available = engine.native.available if name == "adk" else engine.native.task_nodes_available
    if not available:
        required = os.getenv("OWA_TEST_JAVASCRIPT_ENGINE") or os.getenv("OWA_TEST_REQUIRED_ENGINE")
        if required == name:
            pytest.fail("required native JavaScript engine SDK is unavailable")
        pytest.skip("actual independent native SDK is required")


def runtime(tmp_path, backend, *, interpreter=None):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("actual PostgreSQL datasource is required")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "sandbox": {
                **supported_process_limits(),
                "enabled": True,
                "script_runtimes": ["javascript"],
                "workspace_root": str(tmp_path / "workspace"),
                "executable_search_path": interpreter or javascript_path(),
                "inherited_environment": ["SYSTEMROOT"] if os.name == "nt" else [],
                "memory_bytes": None,
                "timeout_seconds": 15,
            },
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path / "runtime"
    )
    assert services.invocations.connection.kind == backend
    return services


def workflow(tasks):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "javascript",
            "name": uuid4().hex,
            "version": "1.0.0",
        },
        "do": tasks,
    }


def handle_for(services, engine_name, plan):
    return services.invocations.create(
        engine=engine_name,
        session_id=uuid4().hex,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
async def test_actual_native_javascript_return_modes_share_one_sandbox_manager(
    tmp_path, backend, engine_name, engine_type
):
    services = runtime(tmp_path, backend)
    engine = engine_type()
    await engine.initialize(services)
    try:
        actual_native(engine, engine_name)
        tasks = []
        modes = ["stdout", "stderr", "code", "all", "none"]
        for mode in modes:
            tasks.append(
                {
                    mode: {
                        "run": {
                            "return": mode,
                            "script": {
                                "language": "javascript",
                                "stdin": "雪",
                                "arguments": ["arg"],
                                "code": (
                                    "const fs=require('node:fs'); "
                                    "process.stdout.write("
                                    "fs.readFileSync(0,'utf8')+process.argv[2]); "
                                    "process.stderr.write('diagnostic');"
                                ),
                            },
                        },
                        "export": {"as": '${ $context + {"' + mode + '": .} }'},
                    }
                }
            )
        tasks.append({"result": {"set": {"data": "${ $context }"}, "output": {"as": "${ .data }"}}})
        plan = compile_sandbox_workflow(workflow(tasks), sandbox=services.config.sandbox)
        handle = handle_for(services, engine_name, plan)
        result = await engine.invoke(plan, handle, {"original": None})
        assert result.status == "completed", result.error
        assert result.output == {
            "original": None,
            "stdout": "雪arg",
            "stderr": "diagnostic",
            "code": 0,
            "all": {"code": 0, "stdout": "雪arg", "stderr": "diagnostic"},
            "none": None,
        }
        assert any(value["count"] for value in native_snapshot(services, handle, backend).values())
        completed = [
            e for e in services.events.events if e.event_type == "SandboxExecutionCompleted"
        ]
        assert len(completed) == len({e.execution_id for e in completed}) == 5
        assert services.model.calls == []
        assert list((tmp_path / "workspace").iterdir()) == []
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("boundary", ["known", "pending"])
def test_actual_stop_never_repeats_a_known_or_uncertain_javascript_commit(
    tmp_path, backend, engine_name, engine_type, boundary
):
    from test_executable_source_process_recovery import ChildIdentity, read_boundary, stop

    if backend == "postgresql" and not os.getenv("OWA_TEST_POSTGRES_URL"):
        pytest.skip("actual PostgreSQL datasource is required")
    actual_native(engine_type(), engine_name)
    javascript_path()
    original = {"subject": uuid4().hex, "null": None}
    (tmp_path / "scenario.json").write_text(
        json.dumps(
            {
                "engine": engine_name,
                "backend": backend,
                "boundary": boundary,
                "input": original,
            }
        ),
        encoding="utf-8",
    )
    worker = Path(__file__).with_name("_javascript_sandbox_recovery_worker.py")

    def launch(mode):
        return subprocess.Popen(
            [getattr(sys, "_base_executable", sys.executable), str(worker), str(tmp_path), mode],
            cwd=tmp_path,
            env=_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    producer = recovery = child = None
    try:
        producer = launch("start")
        ready = read_boundary(tmp_path, "ready.json", producer, "start")
        assert ready["pid"] == producer.pid != os.getpid()
        assert ready["backend"] == backend and ready["input"] == original
        assert ready["version"] == "1.0.0" and ready["model_calls"] == 0
        assert len(ready["business"]) == 1 and ready["business"][0]["input"] == original
        assert ready["business"][0]["pid"] not in {producer.pid, os.getpid()}
        if boundary == "pending":
            child = ChildIdentity(ready["business"][0]["pid"])
        assert ready["receipt_status"] == ("completed" if boundary == "known" else "pending")
        assert any(value["count"] for value in ready["native"].values())
        producer.kill()
        assert producer.wait(timeout=15) != 0 and not (tmp_path / "start-cleaned.json").exists()
        if child is not None:
            # Test cleanup of the held actual OS identity; no orphan isolation claim.
            child.close_and_stop_fixture()
        recovery = launch("recover")
        assert recovery.wait(timeout=100) == 0, (
            (tmp_path / "recover-failure.json").read_text(encoding="utf-8")
            if (tmp_path / "recover-failure.json").exists()
            else "recovery exited"
        )
        result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
        assert result["pid"] == recovery.pid and result["pid"] not in {producer.pid, os.getpid()}
        for key in (
            "invocation_id",
            "session_id",
            "reference",
            "fingerprint",
            "version",
            "input",
            "identity",
            "receipt_status",
            "receipt_digest",
            "clock",
            "business",
        ):
            assert result[key] == ready[key], key
        assert result["generation"] > ready["generation"] and result["model_calls"] == 0
        if boundary == "known":
            assert result["status"] == "completed" and json.loads(result["output"]) == original
        else:
            assert result["status"] == "faulted"
            assert any(
                word in json.dumps(result["error"]).lower()
                for word in ("uncertain", "unresolved", "recovery")
            )
        assert len((tmp_path / "business.jsonl").read_text(encoding="utf-8").splitlines()) == 1
        assert (tmp_path / "recover-cleaned.json").exists()
    finally:
        stop(recovery)
        stop(producer)
        if child is not None:
            child.close_and_stop_fixture()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
async def test_changed_physical_javascript_interpreter_rejects_before_recovery_mutation(
    tmp_path, backend, engine_name, engine_type
):
    node = Path(javascript_path()) / ("node.exe" if os.name == "nt" else "node")
    directory = tmp_path / "interpreter"
    directory.mkdir()
    copied = directory / node.name
    shutil.copy2(node, copied)
    services = runtime(tmp_path, backend, interpreter=str(directory))
    engine = engine_type()
    await engine.initialize(services)
    try:
        actual_native(engine, engine_name)
        public = workflow(
            [
                {
                    "execute": {
                        "run": {"script": {"language": "javascript", "code": "console.log('once')"}}
                    }
                },
                {"pause": {"wait": {"days": 1}}},
            ]
        )
        plan = compile_sandbox_workflow(public, sandbox=services.config.sandbox)
        handle = handle_for(services, engine_name, plan)
        result = await engine.invoke(plan, handle, {"original": None})
        assert result.status == "waiting", result.error
        saved = services.invocations.get(handle.invocation_id)
        before = _common_snapshot(services, handle.invocation_id)
        private = native_snapshot(services, handle, backend)
        with copied.open("ab") as target:
            target.write(b"\x00changed-runtime")
        with pytest.raises(InvocationStateError) as error:
            await engine.resume(saved, None, plan)
        assert error.value.details["reason"] == "runtime_profile_changed"
        assert _common_snapshot(services, handle.invocation_id) == before
        assert native_snapshot(services, handle, backend) == private
        assert services.model.calls == []
    finally:
        await engine.shutdown()
        services.close()
