"""SDK-facing frame bridge rejects untrusted wire data without echoing it."""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager

import pytest
from mcp.shared.message import SessionMessage
from mcp_types import JSONRPCRequest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import ToolError, _McpOutcomeUncertain
from open_workflow_agent.mcp_client import call_mcp
from open_workflow_agent.mcp_stdio import _strict_frame, sandbox_stdio_transport
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.sandbox import InternalSandboxBackend, SandboxManager


@pytest.mark.parametrize(
    "frame",
    [
        b"\xff",
        b"{",
        b"[]",
        b'{"jsonrpc":"2.0","id":1,"id":2}',
        b'{"jsonrpc":"2.0","result":NaN}',
        b'{"jsonrpc":"1.0"}',
    ],
)
def test_invalid_frame_details_never_include_wire_payload(frame):
    with pytest.raises(ToolError) as failure:
        _strict_frame(frame)
    assert str(failure.value) == "MCP stdio response frame is invalid"
    assert not failure.value.details


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_id", ['"other"', "true", "7"])
async def test_sdk_transport_rejects_wrong_or_coerced_response_ids(tmp_path, wrong_id):
    script = (
        "import sys,time; sys.stdin.readline(); "
        f'print(\'{{"jsonrpc":"2.0","id":{wrong_id},"result":{{}}}}\',flush=True); '
        "time.sleep(60)"
    )
    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "workspace_root": str(tmp_path / "workspace"),
            "file_size_bytes": None if os.name == "nt" else 33_554_432,
            "mcp_stdio": {
                "enabled": True,
                "servers": [
                    {"id": "wrong", "command": sys.executable, "arguments": ["-u", "-c", script]}
                ],
            },
        }
    )
    backend = InternalSandboxBackend(config)
    manager = SandboxManager(backend)
    async with sandbox_stdio_transport(
        manager,
        {"stdio": {"command": sys.executable, "arguments": ["-u", "-c", script]}},
        timeout_seconds=3,
    ) as (read, write):
        await write.send(
            SessionMessage(JSONRPCRequest(jsonrpc="2.0", id="7", method="tools/list", params={}))
        )
        result = await asyncio.wait_for(read.receive(), timeout=3)
        assert isinstance(result, ToolError)
        assert str(result) == "MCP stdio response correlation is invalid"
    assert not backend._stdio_sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["remote_error", "lost_response", "startup_close"])
async def test_sdk_distinguishes_correlated_server_error_from_connection_loss(
    tmp_path, outcome, monkeypatch
):
    script = "import sys,json; request=json.loads(sys.stdin.readline()); "
    if outcome == "startup_close":
        script = "import sys; sys.exit(0)"
    if outcome == "remote_error":
        script += (
            "print(json.dumps({'jsonrpc':'2.0','id':request['id'],"
            "'error':{'code':-32000,'message':'Connection closed'}}),flush=True)"
        )
    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "workspace_root": str(tmp_path / "workspace"),
            "file_size_bytes": None if os.name == "nt" else 33_554_432,
            "mcp_stdio": {
                "enabled": True,
                "servers": [
                    {"id": "outcome", "command": sys.executable, "arguments": ["-u", "-c", script]}
                ],
            },
        }
    )
    backend = InternalSandboxBackend(config)
    manager = SandboxManager(backend)
    if outcome == "startup_close":
        original_open = manager.open_stdio

        @asynccontextmanager
        async def stopped_before_dispatch(request):
            async with original_open(request) as session:
                # Actual process/pipe EOF, before the SDK can write anything;
                # the barrier makes the pre-dispatch case deterministic.
                await asyncio.wait_for(session.process.wait(), timeout=3)
                await asyncio.wait_for(asyncio.shield(session.failure), timeout=3)
                yield session

        monkeypatch.setattr(manager, "open_stdio", stopped_before_dispatch)
    expected = _McpOutcomeUncertain if outcome == "lost_response" else ToolError
    with pytest.raises(expected) as failure:
        await call_mcp(
            HttpClient(),
            {
                "method": "tools/list",
                "transport": {
                    "stdio": {"command": sys.executable, "arguments": ["-u", "-c", script]}
                },
            },
            timeout=3,
            sandbox=manager,
        )
    if outcome == "remote_error":
        assert type(failure.value) is ToolError
        assert failure.value.details["reason"] == "protocol_error"
    elif outcome == "startup_close":
        assert type(failure.value) is ToolError
        assert failure.value.details["reason"] == "transport_failure"
    assert not backend._stdio_sessions


@pytest.mark.asyncio
async def test_sdk_accepts_complete_correlated_response_from_one_response_process(tmp_path):
    script = (
        "import sys,json; request=json.loads(sys.stdin.readline()); "
        "print(json.dumps({'jsonrpc':'2.0','id':request['id'],"
        "'result':{'resultType':'complete','tools':[], 'ttlMs':0,"
        "'cacheScope':'public'}}),flush=True)"
    )
    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "workspace_root": str(tmp_path / "workspace"),
            "file_size_bytes": None if os.name == "nt" else 33_554_432,
            "mcp_stdio": {
                "enabled": True,
                "servers": [
                    {"id": "once", "command": sys.executable, "arguments": ["-u", "-c", script]}
                ],
            },
        }
    )
    backend = InternalSandboxBackend(config)
    result = await call_mcp(
        HttpClient(),
        {
            "method": "tools/list",
            "transport": {"stdio": {"command": sys.executable, "arguments": ["-u", "-c", script]}},
        },
        timeout=3,
        sandbox=SandboxManager(backend),
    )
    assert result["resultType"] == "complete" and result["tools"] == []
    assert not backend._stdio_sessions
