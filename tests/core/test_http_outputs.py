"""OWS HTTP byte projection and status selection never authorize redirect I/O."""

from __future__ import annotations

import base64

import httpx
import pytest
from open_workflow_agent.errors import ToolError
from open_workflow_agent.protocols import HttpClient, ProtocolServices


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"\x00\xffbinary", "مرحبا".encode(), b"null"])
async def test_raw_output_retains_exact_received_bytes(content):
    service = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content)))
    )
    encoded = await service.call("http", {"endpoint": "https://http.test", "output": "raw"})
    assert base64.b64decode(encoded, validate=True) == content


@pytest.mark.asyncio
@pytest.mark.parametrize("redirect", [False, True])
@pytest.mark.parametrize("status", [101, 200, 204, 299, 300, 302, 304, 399, 400, 503])
async def test_redirect_selects_status_range_without_visiting_location(status, redirect):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(status, content=b"", headers={"location": "https://other.test"})

    # Even an explicitly constructed client configured for redirects cannot
    # turn the workflow's status-selection property into another request.
    service = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(respond), follow_redirects=True)
    )
    payload = {"endpoint": "https://http.test", "redirect": redirect}
    if 200 <= status < (400 if redirect else 300):
        assert await service.call("http", payload) == ""
    else:
        with pytest.raises(ToolError) as failure:
            await service.call("http", payload)
        assert failure.value.details["status"] == status
    assert len(seen) == 1 and str(seen[0].url) == "https://http.test"


@pytest.mark.asyncio
async def test_invalid_redirect_setting_rejects_before_transport():
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200)

    service = ProtocolServices(HttpClient(transport=httpx.MockTransport(respond)))
    with pytest.raises(ToolError, match="redirect must be a boolean"):
        await service.call("http", {"endpoint": "https://http.test", "redirect": "true"})
    assert seen == []
