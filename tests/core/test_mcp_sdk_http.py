"""Seven official MCP methods use typed SDK and bounded common HTTP semantics."""

from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest
from _mcp_sdk_fixture import METHOD_PARAMS, sdk_http_fixture
from open_workflow_agent import mcp_client
from open_workflow_agent.errors import ToolError, _McpOutcomeUncertain
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig


def payload(method):
    return {
        "protocolVersion": "2026-07-28",
        "method": method,
        "parameters": copy.deepcopy(METHOD_PARAMS[method]),
        "client": {"name": "owa-fixture", "version": "1.0.0"},
        "transport": {"http": {"endpoint": "https://fixture.test/mcp"}},
        "operation_id": "business-key",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("package", ["mcp", "mcp-types"])
async def test_unreviewed_installed_sdk_rejects_before_transport_or_auth(monkeypatch, package):
    calls = []
    monkeypatch.setattr(
        mcp_client, "distribution_version", lambda name: "2.4.0" if name == package else "2.3.0"
    )
    services = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    )
    with pytest.raises(ToolError, match="unsupported MCP SDK installation") as error:
        await services.call("mcp", payload("tools/call"))
    assert error.value.details["available"][package] == "2.4.0" and calls == []


@pytest.mark.asyncio
async def test_named_authenticated_redirect_rejects_before_credential_preparation_or_dispatch():
    calls = []

    class Authentication:
        def headers(self, endpoint):
            raise AssertionError("credentials must not be resolved")

        async def prepare(self, endpoint, *, remaining_timeout):
            raise AssertionError("credentials must not be resolved")

    http = HttpClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    with pytest.raises(ToolError, match="redirects"):
        await http.request(
            "GET", "https://fixture.test", follow_redirects=True, _authentication=Authentication()
        )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [False, True], ids=["global", "per-call"])
async def test_selected_custom_api_key_is_redacted_from_response_request_metadata(
    monkeypatch, selected
):
    monkeypatch.setenv("OWA_MCP_METADATA_KEY", "private-deployment-key")
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "key": {
                    "type": "api_key",
                    "header": "X-Deployment-Key",
                    "key": {"from_env": "OWA_MCP_METADATA_KEY"},
                }
            }
        }
    )
    authentication = ProfileAuthentication(security, "key")
    calls = []

    async def receiver(request):
        calls.append(request)
        assert request.headers["X-Deployment-Key"] == "private-deployment-key"
        return httpx.Response(200, json={"ordinary": True})

    client = HttpClient(
        transport=httpx.MockTransport(receiver),
        authentication=None if selected else authentication,
    )
    result = await client.request(
        "GET",
        "https://fixture.test",
        output="response",
        operation_id="business-id",
        headers={"x-deployment-key": "forged", "X-Ordinary": "visible"},
        _authentication=authentication if selected else None,
    )
    assert len(calls) == 1 and result["body"] == {"ordinary": True}
    recorded = {name.lower(): value for name, value in result["request"]["headers"].items()}
    assert "x-deployment-key" not in recorded and recorded["x-ordinary"] == "visible"
    assert recorded["idempotency-key"] == "business-id"
    assert "private-deployment-key" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", METHOD_PARAMS)
@pytest.mark.parametrize("network", [False, True], ids=["asgi-wire", "actual-tcp-sdk"])
async def test_official_sdk_accepts_each_method_metadata_and_result_projection(method, network):
    async with sdk_http_fixture(network=network) as (transport, calls, endpoint):
        value = payload(method)
        value["transport"]["http"]["endpoint"] = endpoint
        result = await ProtocolServices(
            HttpClient(
                transport=transport,
                allowed_hosts={"127.0.0.1"} if network else {"fixture.test"},
            )
        ).call("mcp", value)
    assert len(calls) == 1
    body, headers = calls[0]["body"], calls[0]["headers"]
    assert body["method"] == method and body["id"] == "business-key"
    meta = body["params"]["_meta"]
    assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    assert meta["io.modelcontextprotocol/clientInfo"] == {"name": "owa-fixture", "version": "1.0.0"}
    assert isinstance(meta["io.modelcontextprotocol/clientCapabilities"], dict)
    assert headers[b"mcp-method"].decode() == method
    assert headers[b"idempotency-key"] == b"business-key"
    assert "jsonrpc" not in result and "id" not in result and result["resultType"] == "complete"
    if method == "tools/call":
        assert result["structuredContent"] == {"value": 7}
    elif method.endswith("/list"):
        assert any(key in result for key in ("tools", "prompts", "resources", "resourceTemplates"))


@pytest.mark.asyncio
@pytest.mark.parametrize("method", METHOD_PARAMS)
@pytest.mark.parametrize(
    "failure", ["protocol_error", "wrong_id", "invalid_result", "size", "timeout"]
)
async def test_every_method_rejects_errors_limits_and_uncorrelated_results(method, failure):
    calls = []

    async def receiver(request):
        body = json.loads(request.content)
        calls.append(body)
        if failure == "timeout":
            await asyncio.sleep(0.15)
        if failure == "protocol_error":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "error": {"code": -32602, "message": "private receiver text"},
                },
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "foreign" if failure == "wrong_id" else body["id"],
                "result": {"bad": "x" * (500 if failure == "size" else 1)},
            },
        )

    services = ProtocolServices(
        HttpClient(
            transport=httpx.MockTransport(receiver),
            timeout=0.05 if failure == "timeout" else 2,
            max_response_bytes=200 if failure == "size" else 4000,
        )
    )
    with pytest.raises(ToolError if failure == "protocol_error" else _McpOutcomeUncertain) as error:
        await services.call("mcp", payload(method))
    assert len(calls) == 1 and "private receiver text" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["version", "reserved_meta", "method", "tasks", "bad_parameters", "boolean_id"]
)
async def test_rejected_admission_never_dispatches_or_resolves_credentials(change):
    calls = []
    value = payload("tools/call")
    if change == "version":
        value["protocolVersion"] = "2025-06-18"
    elif change == "reserved_meta":
        value["parameters"]["_meta"] = {"io.modelcontextprotocol/protocolVersion": "forged"}
    elif change == "method":
        value["method"] = "server/discover"
    elif change == "tasks":
        value["method"] = "tasks/get"
    elif change == "bad_parameters":
        value["parameters"] = {"name": 123}
    else:
        value["id"] = True
    services = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    )
    with pytest.raises(ToolError):
        await services.call("mcp", value)
    assert calls == []


@pytest.mark.asyncio
async def test_explicit_null_and_tool_error_remain_distinct_without_hidden_replay():
    calls = []

    async def receiver(request):
        body = json.loads(request.content)
        calls.append(body)
        result = {
            "resultType": "complete",
            "content": [],
            "structuredContent": None,
            "isError": len(calls) == 2,
        }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    assert (await services.call("mcp", payload("tools/call")))["structuredContent"] is None
    with pytest.raises(ToolError):
        await services.call("mcp", payload("tools/call"))
    assert len(calls) == 2 and all(call["method"] == "tools/call" for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "valid",
        "multiple",
        "notification",
        "unknown_event",
        "incomplete",
        "duplicate_json",
        "oversized",
        "invalid_utf8",
    ],
)
async def test_selected_sse_response_is_finite_correlated_and_never_replayed(mode):
    calls = []

    async def receiver(request):
        body = json.loads(request.content)
        calls.append(body)
        message = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"resultType": "complete", "content": [], "structuredContent": None},
            }
        )
        frame = "event: message\ndata: " + message + "\n\n"
        if mode == "multiple":
            frame *= 2
        elif mode == "notification":
            frame = 'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n' + frame
        elif mode == "unknown_event":
            frame = frame.replace("event: message", "event: other")
        elif mode == "incomplete":
            frame = frame.rstrip("\n")
        elif mode == "duplicate_json":
            frame = (
                'data: {"jsonrpc":"2.0","id":"business-key","id":"business-key","result":{}}\n\n'
            )
        elif mode == "oversized":
            frame = 'data: {"padding":"' + "x" * 1_048_576 + '"}\n\n'
        data = b"\xff\n\n" if mode == "invalid_utf8" else frame.encode()
        return httpx.Response(200, content=data, headers={"Content-Type": "text/event-stream"})

    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    if mode == "valid":
        assert (await services.call("mcp", payload("tools/call")))["structuredContent"] is None
    else:
        with pytest.raises(_McpOutcomeUncertain):
            await services.call("mcp", payload("tools/call"))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_authentication_failure_is_known_before_mcp_business_dispatch():
    calls = []

    class RejectedAuthentication:
        def headers(self, endpoint):
            raise AssertionError("asynchronous preparation required")

        async def prepare(self, endpoint, *, remaining_timeout):
            raise ToolError("protocol authentication failed", details={"stage": "authentication"})

    services = ProtocolServices(
        HttpClient(
            authentication=RejectedAuthentication(),
            transport=httpx.MockTransport(lambda request: calls.append(request)),
        )
    )
    with pytest.raises(ToolError) as error:
        await services.call("mcp", payload("tools/call"))
    assert not isinstance(error.value, _McpOutcomeUncertain)
    assert error.value.details == {"stage": "authentication"} and calls == []


@pytest.mark.asyncio
async def test_original_http_deadline_bounds_dns_before_dispatch_callback():
    resolving = asyncio.Event()
    cancelled = asyncio.Event()
    dispatched = []

    async def resolver(endpoint):
        resolving.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client = HttpClient(timeout=0.03, destination_resolver=resolver)
    with pytest.raises(ToolError, match="deadline"):
        await client.request(
            "POST", "https://fixture.test", _on_dispatch=lambda: dispatched.append(True)
        )
    assert resolving.is_set() and cancelled.is_set() and dispatched == []


@pytest.mark.asyncio
async def test_http_body_consumption_is_bounded_after_actual_send_and_closed():
    dispatched = []
    calls = []
    closed = asyncio.Event()
    cancelled = asyncio.Event()

    class StalledBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first"
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def aclose(self):
            closed.set()

    async def receiver(request):
        calls.append(request)
        assert dispatched == [True]
        return httpx.Response(200, stream=StalledBody())

    client = HttpClient(timeout=0.03, transport=httpx.MockTransport(receiver))
    with pytest.raises(ToolError, match="deadline"):
        await client.request(
            "POST", "https://fixture.test", _on_dispatch=lambda: dispatched.append(True)
        )
    assert len(calls) == 1 and cancelled.is_set() and closed.is_set()


@pytest.mark.asyncio
async def test_sdk_dispatch_attempt_cannot_repeat_after_known_preflight_failure():
    calls = []
    dispatcher = mcp_client._CommonHttpDispatcher(
        HttpClient(
            allowed_hosts={"approved.test"},
            transport=httpx.MockTransport(lambda request: calls.append(request)),
        ),
        "https://fixture.test/mcp",
        {},
        operation_id="business-key",
        request_id="business-key",
        timeout=1,
        authentication=None,
    )
    with pytest.raises(ToolError, match="host is not allowed"):
        await dispatcher.send_raw_request("tools/list", {})
    assert not dispatcher._dispatched and calls == []
    with pytest.raises(ToolError) as error:
        await dispatcher.send_raw_request("tools/list", {})
    assert error.value.details["reason"] == "unexpected_dispatch" and calls == []


@pytest.mark.asyncio
async def test_mcp_resource_timeout_after_send_preserves_unknown_outcome_and_closes_stream():
    calls = []
    closed = asyncio.Event()

    class StalledResponse(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            closed.set()

    async def receiver(request):
        calls.append(request)
        return httpx.Response(
            200, headers={"Content-Type": "application/json"}, stream=StalledResponse()
        )

    services = ProtocolServices(HttpClient(timeout=0.03, transport=httpx.MockTransport(receiver)))
    with pytest.raises(_McpOutcomeUncertain):
        await services.call("mcp", payload("tools/call"))
    assert len(calls) == 1 and closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("network", [False, True], ids=["asgi-wire", "actual-tcp-sdk"])
@pytest.mark.parametrize(
    "method", ["tools/list", "prompts/list", "resources/list", "resources/templates/list"]
)
async def test_actual_sdk_pagination_preserves_explicit_cursor_without_hidden_page_fetches(
    method, network
):
    async with sdk_http_fixture(network=network, pagination=True) as (transport, calls, endpoint):
        services = ProtocolServices(
            HttpClient(
                transport=transport,
                allowed_hosts={"127.0.0.1"} if network else {"fixture.test"},
            )
        )
        request = payload(method)
        request["transport"]["http"]["endpoint"] = endpoint
        first = await services.call("mcp", request)
        assert first["nextCursor"] == "opaque-page-2" and len(calls) == 1
        assert "cursor" not in calls[0]["body"]["params"]
        request["parameters"]["cursor"] = first["nextCursor"]
        request["operation_id"] = "explicit-second-page"
        second = await services.call("mcp", request)
        assert "nextCursor" not in second and len(calls) == 2
        assert calls[1]["body"]["params"]["cursor"] == "opaque-page-2"
        assert [call["body"]["id"] for call in calls] == ["business-key", "explicit-second-page"]
        assert all(call["body"]["method"] == method for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("network", [False, True], ids=["asgi-wire", "actual-tcp-sdk"])
async def test_actual_sdk_input_required_is_uncertain_without_fabricated_response_or_replay(
    network,
):
    async with sdk_http_fixture(network=network, input_required=True) as (
        transport,
        calls,
        endpoint,
    ):
        services = ProtocolServices(
            HttpClient(
                transport=transport,
                allowed_hosts={"127.0.0.1"} if network else {"fixture.test"},
            )
        )
        request = payload("tools/call")
        request["transport"]["http"]["endpoint"] = endpoint
        with pytest.raises(_McpOutcomeUncertain) as error:
            await services.call("mcp", request)
        assert error.value.details["reason"] == "unsupported_interaction"
        assert len(calls) == 1 and calls[0]["body"]["method"] == "tools/call"
        assert "inputResponses" not in calls[0]["body"]["params"]
        assert "requestState" not in calls[0]["body"]["params"]
        assert "opaque-server-continuation" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("network", [False, True], ids=["asgi-wire", "actual-tcp-sdk"])
async def test_actual_sdk_selected_sse_result_is_bounded_and_has_one_business_request(network):
    async with sdk_http_fixture(network=network, sse=True) as (transport, calls, endpoint):
        services = ProtocolServices(
            HttpClient(
                transport=transport,
                timeout=3,
                max_response_bytes=4096,
                allowed_hosts={"127.0.0.1"} if network else {"fixture.test"},
            )
        )
        request = payload("tools/call")
        request["transport"]["http"]["endpoint"] = endpoint
        result = await services.call("mcp", request)
        assert result["structuredContent"] == {"value": 7}
        assert result["resultType"] == "complete" and "jsonrpc" not in result
        assert len(calls) == 1 and calls[0]["body"]["id"] == "business-key"
        assert calls[0]["headers"][b"accept"] == b"application/json, text/event-stream"
        assert calls[0]["body"]["method"] == "tools/call"
