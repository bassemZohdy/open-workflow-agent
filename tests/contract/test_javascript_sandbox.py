"""Shared actual-native JavaScript return and interpreter recovery fixtures."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from _version_recovery_worker import native_snapshot
from engine_cases import engine_cases
from javascript_fixture_policy import javascript_path
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.sandbox import compile_sandbox_workflow
from open_workflow_agent.services import RuntimeServices
from sandbox_fixture_policy import supported_process_limits
from test_execution_authority_isolation import actual_native
from test_workflow_version_recovery import _common_snapshot

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")
ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]


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
        assert any(native_snapshot(services, handle, backend).values())
        completed = [
            e for e in services.events.events if e.event_type == "SandboxExecutionCompleted"
        ]
        assert len(completed) == len({e.execution_id for e in completed}) == 5
        assert services.model.calls == []
        assert list((tmp_path / "workspace").iterdir()) == []
    finally:
        await engine.shutdown()
        services.close()


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
