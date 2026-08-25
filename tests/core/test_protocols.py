from __future__ import annotations

import json

import httpx
import pytest
from open_workflow_agent.protocols import HttpClient, ProtocolServices


@pytest.mark.asyncio
async def test_protocol_services_build_mcp_json_rpc_request():
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"result": {"ok": True}})

    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    result = await services.call(
        "mcp", {"endpoint": "https://mcp.test", "name": "lookup", "arguments": {"q": "x"}}
    )
    assert result["result"]["ok"] is True
    assert seen["method"] == "tools/call"
