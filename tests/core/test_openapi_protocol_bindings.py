"""Deployment route bindings and original operation budgets before business I/O."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from open_workflow_agent.config import ToolConfig
from open_workflow_agent.errors import ToolError
from open_workflow_agent.openapi_client import (
    OpenApiDescription,
    _OpenApiOutcomeUncertain,
    select_operation,
)
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.tools import ToolRegistry
from openapi_fixtures import operation_document


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", ["https://api.test/selected", "https://api.test/other"])
async def test_configured_openapi_endpoint_constrains_actual_selected_route(bound):
    document = operation_document("https://api.test", path="/selected")
    description = OpenApiDescription.capture("https://description.test/spec", document)
    calls = []

    def receive(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"selected": True})

    registry = ToolRegistry.from_config(
        [
            ToolConfig(
                type="openapi",
                name="selected",
                endpoint=bound,
                options={"operationId": "lookup"},
                openapi_binding={
                    "id": "selected",
                    "version": "1",
                    "document_uri": description.uri,
                    "document": document,
                    "digest": description.digest,
                },
            )
        ],
        ProtocolServices(HttpClient(transport=httpx.MockTransport(receive))),
    )
    if bound.endswith("/other"):
        with pytest.raises(ToolError):
            await registry.invoke("selected", {})
        assert calls == []
    else:
        assert await registry.invoke("selected", {}) == {"selected": True}
        assert calls == [bound]


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [True, 0, -1, float("nan"), float("inf"), "unbounded"])
async def test_invalid_direct_openapi_budget_rejects_before_description_io(budget):
    calls = []
    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(calls.append)))
    with pytest.raises(ToolError, match="timeout"):
        await services.call(
            "openapi",
            {
                "operationId": "lookup",
                "document": {"endpoint": "https://description.test/spec"},
                "timeout": budget,
            },
        )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("captured", [False, True])
@pytest.mark.parametrize("budget", [1, "1s", {"seconds": 1}])
async def test_direct_openapi_retains_requested_budget_through_description_and_body(
    captured, budget
):
    document = operation_document("https://api.test")
    selection = select_operation(
        OpenApiDescription.capture("https://description.test/spec", document), "lookup"
    )
    entered = asyncio.Event()
    calls, closed = [], []

    class HeldBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            closed.append(True)

    async def receive(request):
        calls.append(request)
        if request.url.host == "description.test":
            await asyncio.sleep(0.3)
            return httpx.Response(200, json=document)
        assert 0 < request.extensions["timeout"]["read"] <= (1 if captured else 0.85)
        return httpx.Response(200, stream=HeldBody())

    services = ProtocolServices(HttpClient(timeout=10, transport=httpx.MockTransport(receive)))
    started = time.monotonic()
    with pytest.raises(_OpenApiOutcomeUncertain):
        await services.call(
            "openapi",
            {
                "operationId": "lookup",
                "document": {"endpoint": "https://description.test/spec"},
                "timeout": budget,
            },
            _openapi_selection=selection if captured else None,
        )
    assert entered.is_set() and closed == [True]
    assert len([r for r in calls if r.url.host == "api.test"]) == 1
    assert time.monotonic() - started < 3
