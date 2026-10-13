"""Seven modern methods on a pinned official server through the common sandbox."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig, SandboxConfig
from open_workflow_agent.mcp_client import call_mcp
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.sandbox import (
    InternalSandboxBackend,
    SandboxManager,
    compile_sandbox_workflow,
)
from open_workflow_agent.services import RuntimeServices

SERVER = Path(__file__).with_name("_mcp_stdio_sdk_server.py")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,parameters,field",
    [
        ("tools/list", {}, "tools"),
        ("tools/call", {"name": "echo", "arguments": {"value": None}}, "structuredContent"),
        ("prompts/list", {}, "prompts"),
        ("prompts/get", {"name": "hello"}, "messages"),
        ("resources/list", {}, "resources"),
        ("resources/read", {"uri": "test://resource"}, "contents"),
        ("resources/templates/list", {}, "resourceTemplates"),
    ],
)
async def test_actual_sdk_stdio_methods_use_only_common_process_transport(
    tmp_path, method, parameters, field
):
    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "workspace_root": str(tmp_path / "workspace"),
            "timeout_seconds": 15,
            "file_size_bytes": None if os.name == "nt" else 33_554_432,
            "mcp_stdio": {
                "enabled": True,
                "servers": [
                    {
                        "id": "official-fixture",
                        "command": sys.executable,
                        "arguments": ["-u", str(SERVER)],
                    }
                ],
            },
        }
    )
    backend = InternalSandboxBackend(config)
    manager = SandboxManager(backend)
    result = await call_mcp(
        HttpClient(),
        {
            "method": method,
            "parameters": parameters,
            "protocolVersion": "2026-07-28",
            "transport": {"stdio": {"command": sys.executable, "arguments": ["-u", str(SERVER)]}},
        },
        timeout=15,
        operation_id="stdio-method-" + method.replace("/", "-"),
        sandbox=manager,
    )
    assert result["resultType"] == "complete" and field in result
    if method == "tools/call":
        assert result["structuredContent"] == {"value": None}
    assert not backend._stdio_sessions and not backend._stdio_starting
    assert list(Path(config.workspace_root).iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("explicit_workflow_delivery")
@pytest.mark.parametrize(
    "engine_name,engine_type", [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]
)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_actual_native_stdio_known_outcome_replays_without_another_server_call(
    tmp_path, engine_name, engine_type, backend
):
    engine = engine_type()
    available = (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    )
    if not available:
        pytest.skip("actual independent native environment is required")
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    ledger = tmp_path / "receiver.jsonl"
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "sandbox": {
                "enabled": True,
                "workspace_root": str(tmp_path / "workspace"),
                "timeout_seconds": 15,
                "file_size_bytes": None if os.name == "nt" else 33_554_432,
                "mcp_stdio": {
                    "enabled": True,
                    "servers": [
                        {
                            "id": "official-fixture",
                            "command": sys.executable,
                            "arguments": ["-u", str(SERVER)],
                            "environment": {"LEDGER": str(ledger)},
                        }
                    ],
                },
            },
        }
    )
    plan = compile_sandbox_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "stdio",
                "name": "native",
                "version": "1.0.0",
            },
            "do": [
                {
                    "call": {
                        "call": "mcp",
                        "with": {
                            "method": "tools/call",
                            "protocolVersion": "2026-07-28",
                            "transport": {
                                "stdio": {
                                    "command": sys.executable,
                                    "arguments": ["-u", str(SERVER)],
                                }
                            },
                            "parameters": {
                                "name": "echo",
                                "arguments": {"value": "${ $workflow.input }"},
                            },
                        },
                    }
                },
                {"pause": {"wait": {"seconds": 600}}},
            ],
        },
        sandbox=config.sandbox,
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
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
        result = await engine.invoke(plan, handle, None)
        assert result.status == "waiting", result.error
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert len(rows) == 1 and rows[0]["input"] == {"value": None}
        await engine.shutdown()
        services.close()
        services = RuntimeServices(
            config, model=FakeModel(), database_root=None if datasource else tmp_path
        )
        engine = engine_type()
        await engine.initialize(services)
        saved = services.invocations.get(handle.invocation_id)
        result = await engine.resume(saved, None, plan)
        assert result.status == "completed", result.error
        assert result.output["structuredContent"] == {"value": None}
        assert result.invocation_id == handle.invocation_id
        assert [json.loads(line) for line in ledger.read_text().splitlines()] == rows
        assert not services.model.calls
        assert not services.sandbox.backend._stdio_sessions
    finally:
        await engine.shutdown()
        services.close()
