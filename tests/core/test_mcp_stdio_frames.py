"""SDK-facing frame bridge rejects untrusted wire data without echoing it."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from mcp.shared.message import SessionMessage
from mcp_types import JSONRPCRequest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import ToolError
from open_workflow_agent.mcp_stdio import _strict_frame, sandbox_stdio_transport
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
