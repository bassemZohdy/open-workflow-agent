"""Finite common A2A v1 calls interoperate with an independent official SDK."""

from __future__ import annotations

import asyncio
import copy
import json
import ssl
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

import httpx
import pytest
from _a2a_sdk_fixture import peer_card, sdk_a2a_fixture
from a2a.types import a2a_pb2 as a2a_types
from google.protobuf import json_format
from open_workflow_agent import _a2a_types
from open_workflow_agent.a2a_client import A2ACardSelection, fetch_validate_card, select_interface
from open_workflow_agent.errors import ToolError, _A2AOutcomeUncertain
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig
from open_workflow_agent.tools import ToolDefinition, ToolRegistry

METHODS = {
    "SendMessage": {"message": {"parts": [{"text": "hello"}]}},
    "GetTask": {"id": "task-id"},
    "CancelTask": {"id": "task-id"},
    "ListTasks": {},
    "GetExtendedAgentCard": {},
    "SendStreamingMessage": {"message": {"parts": [{"text": "hello"}]}},
    "SubscribeToTask": {"id": "task-id"},
}


def payload(method, endpoint):
    return {
        "method": method,
        "server": endpoint,
        "parameters": copy.deepcopy(METHODS[method]),
        "operation_id": "original-business-key",
    }


def client(transport, network, **kwargs):
    return HttpClient(
        transport=transport, allowed_hosts={"127.0.0.1"} if network else {"a2a.test"}, **kwargs
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("network", [False, True], ids=["asgi-wire", "actual-tcp-sdk"])
@pytest.mark.parametrize("method", METHODS)
async def test_each_method_uses_actual_v1_sdk_envelopes_identity_and_single_business_dispatch(
    network, method
):
    async with sdk_a2a_fixture(network=network) as (transport, calls, endpoint):
        result = await ProtocolServices(client(transport, network)).call(
            "a2a", payload(method, endpoint)
        )
        assert len(calls) == 1 and calls[0]["body"]["method"] == method
        assert calls[0]["body"]["id"] == "original-business-key"
        assert calls[0]["headers"][b"a2a-version"] == b"1.0"
        assert calls[0]["headers"][b"idempotency-key"] == b"original-business-key"
        if method in {"SendMessage", "SendStreamingMessage"}:
            message = calls[0]["body"]["params"]["message"]
            assert message["role"] == "ROLE_USER"
            assert message["messageId"] == str(
                uuid5(NAMESPACE_URL, "owa/a2a-message/v1:original-business-key")
            )
        if method in {"SendStreamingMessage", "SubscribeToTask"}:
            assert result[0]["task"]["status"]["state"] == "TASK_STATE_WORKING"
            assert result[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
            assert all("jsonrpc" not in event for event in result)
        else:
            assert isinstance(result, dict) and "jsonrpc" not in result and "result" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("network", [False, True], ids=["asgi-wire", "actual-tcp-sdk"])
@pytest.mark.parametrize("outcome", ["task", "message"])
async def test_sdk_task_and_message_results_preserve_explicit_null_parts(network, outcome):
    async with sdk_a2a_fixture(network=network, outcome=outcome) as (transport, calls, endpoint):
        value = payload("SendMessage", endpoint)
        value["parameters"]["message"]["parts"] = [{"data": None}]
        result = await ProtocolServices(client(transport, network)).call("a2a", value)
        parts = (
            result["task"]["artifacts"][0]["parts"]
            if outcome == "task"
            else result["message"]["parts"]
        )
        assert parts == [{"data": None}] and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("network", [False, True], ids=["asgi-wire", "actual-tcp-sdk"])
@pytest.mark.parametrize(
    "damage", ["foreign-id", "id-type", "both-outcomes", "invalid-task", "invalid-result"]
)
async def test_remote_acceptance_with_invalid_reply_remains_uncertain_without_replay(
    network, damage
):
    def corrupt(response):
        if damage == "foreign-id":
            response["id"] = "foreign"
        elif damage == "id-type":
            response["id"] = 1
        elif damage == "both-outcomes":
            response["error"] = {"code": -32001, "message": "private receiver error"}
        elif damage == "invalid-task":
            response["result"]["task"]["status"]["state"] = "unknown"
        else:
            response["result"] = {"fabricated": True}
        return response

    async with sdk_a2a_fixture(network=network, response_mutation=corrupt) as (
        transport,
        calls,
        endpoint,
    ):
        with pytest.raises(_A2AOutcomeUncertain) as error:
            await ProtocolServices(client(transport, network)).call(
                "a2a", payload("SendMessage", endpoint)
            )
        assert len(calls) == 1 and calls[0]["body"]["method"] == "SendMessage"
        assert "private receiver error" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("network", [False, True], ids=["asgi-wire", "actual-tcp-sdk"])
async def test_correlated_official_task_not_found_is_known_sanitized_protocol_fault(network):
    async with sdk_a2a_fixture(network=network) as (transport, calls, endpoint):
        value = payload("GetTask", endpoint)
        value["parameters"]["id"] = "unknown-private-task"
        with pytest.raises(ToolError) as error:
            await ProtocolServices(client(transport, network)).call("a2a", value)
        assert not isinstance(error.value, _A2AOutcomeUncertain)
        assert (
            error.value.details["reason"] == "protocol_error"
            and error.value.details["code"] == -32001
        )
        assert "unknown-private-task" not in str(error.value) and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "sdk",
        "version",
        "push",
        "grpc",
        "role",
        "parts",
        "boolean-id",
        "reserved-card-flag",
        "mutable-card",
    ],
)
async def test_unqualified_or_invalid_profile_rejects_before_authentication_or_network(
    monkeypatch, damage
):
    calls = []

    class Authentication:
        def headers(self, endpoint):
            raise AssertionError("credentials must not be resolved")

        async def prepare(self, endpoint, *, remaining_timeout):
            raise AssertionError("credentials must not be resolved")

    value = payload("SendMessage", "https://a2a.test/a2a")
    if damage == "sdk":
        monkeypatch.setattr(_a2a_types, "version", lambda name: "1.3.0")
    elif damage == "version":
        value["protocolVersion"] = "0.3"
    elif damage == "push":
        value["method"] = "CreateTaskPushNotificationConfig"
    elif damage == "grpc":
        card = json_format.MessageToDict(peer_card("https://a2a.test/a2a"))
        card["supportedInterfaces"][0]["protocolBinding"] = "GRPC"
        value["agentCard"] = card
    elif damage == "role":
        value["parameters"]["message"]["role"] = "ROLE_UNSPECIFIED"
    elif damage == "parts":
        value["parameters"]["message"]["parts"] = [{"text": "x", "data": {}}]
    elif damage == "boolean-id":
        value["id"] = True
    elif damage == "reserved-card-flag":
        value["_a2a_selection"] = {"endpoint": "https://untrusted.test"}
    else:
        value["agentCard"] = {"endpoint": "https://a2a.test/.well-known/agent-card.json"}
    services = ProtocolServices(
        HttpClient(
            transport=httpx.MockTransport(lambda request: calls.append(request)),
            authentication=Authentication(),
        )
    )
    with pytest.raises(ToolError) as error:
        await services.call("a2a", value)
    assert not isinstance(error.value, _A2AOutcomeUncertain) and calls == []


@pytest.mark.asyncio
async def test_failed_credential_preparation_is_known_before_business_send():
    calls = []

    class Authentication:
        def headers(self, endpoint):
            raise AssertionError("asynchronous preparation required")

        async def prepare(self, endpoint, *, remaining_timeout):
            raise ToolError("protocol authentication failed", details={"stage": "authentication"})

    services = ProtocolServices(
        HttpClient(
            transport=httpx.MockTransport(lambda request: calls.append(request)),
            authentication=Authentication(),
        )
    )
    with pytest.raises(ToolError) as error:
        await services.call("a2a", payload("SendMessage", "https://a2a.test/a2a"))
    assert not isinstance(error.value, _A2AOutcomeUncertain)
    assert error.value.details == {"stage": "authentication"} and calls == []


@pytest.mark.asyncio
async def test_post_send_timeout_closes_body_and_preserves_uncertainty():
    calls = []
    closed = asyncio.Event()
    body_entered = asyncio.Event()

    class StalledBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            body_entered.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            closed.set()

    async def receiver(request):
        calls.append(request)
        return httpx.Response(
            200, headers={"Content-Type": "application/json"}, stream=StalledBody()
        )

    services = ProtocolServices(HttpClient(timeout=1, transport=httpx.MockTransport(receiver)))
    running = asyncio.create_task(
        services.call("a2a", payload("SendMessage", "https://a2a.test/a2a"))
    )
    try:
        await asyncio.wait_for(body_entered.wait(), 2)
        with pytest.raises(_A2AOutcomeUncertain):
            await asyncio.wait_for(running, 2)
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert len(calls) == 1 and closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("network", [False, True], ids=["asgi-wire", "actual-tcp-sdk"])
async def test_public_card_fetch_has_no_inherited_business_credentials_and_uses_pinned_selection(
    network,
):
    async with sdk_a2a_fixture(network=network) as (transport, calls, endpoint):

        class ForbiddenAuthentication:
            def headers(self, endpoint):
                raise AssertionError("business credentials must not reach discovery")

        http = client(
            transport,
            network,
            authentication=ForbiddenAuthentication(),
            client_cert=("unavailable-business-cert", "unavailable-business-key"),
        )
        origin = endpoint.removesuffix("/a2a")
        card = await fetch_validate_card(
            http, {"endpoint": origin + "/.well-known/agent-card.json"}, timeout=2
        )
        assert (
            len(calls) == 1
            and calls[0]["method"] == "GET"
            and b"authorization" not in calls[0]["headers"]
        )
        selection = select_interface(card, explicit_endpoint="https://ignored.test/a2a")
        assert selection.endpoint == endpoint
        restored = A2ACardSelection.from_value(selection.as_value())
        assert restored == selection and "remaining_timeout" not in selection.as_value()
        assert replace(selection, remaining_timeout=0.01).as_value() == selection.as_value()


@pytest.mark.parametrize(
    "field", ["endpoint", "card_json", "card_digest", "binding", "protocol_version"]
)
def test_corrupt_card_selection_rejects_without_refetch_or_credential_preparation(field):
    value = select_interface(
        json_format.MessageToDict(peer_card("https://a2a.test/a2a"))
    ).as_value()
    value[field] = "corrupt"
    with pytest.raises(ToolError, match="A2A call failed"):
        A2ACardSelection.from_value(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["status", "size", "timeout", "redirect", "type", "malformed"])
async def test_card_fetch_is_bounded_and_sanitized_without_business_dispatch(failure):
    calls = []

    async def receiver(request):
        calls.append(request)
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://private.test/card"})
        if failure == "status":
            return httpx.Response(500, text="private-card-secret")
        if failure == "size":
            return httpx.Response(
                200, content=b"x" * 1_048_577, headers={"Content-Type": "application/json"}
            )
        if failure == "type":
            return httpx.Response(200, text="private-card-secret")
        return httpx.Response(200, json={"private": "private-card-secret"})

    with pytest.raises(ToolError) as error:
        await fetch_validate_card(
            HttpClient(transport=httpx.MockTransport(receiver)),
            {"endpoint": "https://a2a.test/card"},
            timeout=2,
        )
    assert len(calls) == 1 and calls[0].method == "GET"
    assert "private-card-secret" not in str(error.value)
    assert "private-card-secret" not in json.dumps(error.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["size", "foreign-task", "after-terminal", "incomplete", "notification"]
)
async def test_stream_limits_identity_and_completion_are_checked_without_reconnect(failure):
    calls = []

    async def receiver(request):
        body = json.loads(request.content)
        calls.append(body)
        task = {
            "task": {
                "id": "task-id",
                "contextId": "context-id",
                "status": {"state": "TASK_STATE_WORKING"},
            }
        }
        final = {
            "statusUpdate": {
                "taskId": "task-id",
                "contextId": "context-id",
                "status": {"state": "TASK_STATE_COMPLETED"},
            }
        }
        values = [task, final]
        if failure == "foreign-task":
            final["statusUpdate"]["taskId"] = "foreign"
        elif failure == "after-terminal":
            values.append(final)
        elif failure == "incomplete":
            values = [task]
        frames = "".join(
            "data: " + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": value}) + "\n\n"
            for value in values
        )
        if failure == "notification":
            frames = 'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n' + frames
        elif failure == "size":
            frames += ":" + "x" * 8192 + "\n\n"
        return httpx.Response(
            200, content=frames.encode(), headers={"Content-Type": "text/event-stream"}
        )

    services = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(receiver), max_response_bytes=4096)
    )
    with pytest.raises(_A2AOutcomeUncertain):
        await services.call("a2a", payload("SendStreamingMessage", "https://a2a.test/a2a"))
    assert len(calls) == 1


def named_authentication(kind, http):
    profiles = {
        "bearer": {"type": "bearer", "token": {"from_env": "A2A_TEST_SECRET"}},
        "api_key": {"type": "api_key", "key": {"from_env": "A2A_TEST_SECRET"}},
        "mtls": {
            "type": "mtls",
            "certificate": {"from_env": "A2A_TEST_CERT"},
            "private_key": {"from_env": "A2A_TEST_KEY"},
        },
        "oauth": {
            "type": "oauth2_client_credentials",
            "token_url": "https://issuer.test/token",
            "client_id": {"from_env": "A2A_TEST_ID"},
            "client_secret": {"from_env": "A2A_TEST_SECRET"},
            "allowed_resource_endpoints": ["https://a2a.test/authorized"],
        },
    }
    return ProfileAuthentication(
        SecurityConfig.model_validate({"profiles": {"selected": profiles[kind]}}),
        "selected",
        token_http=http,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bearer", "api_key", "mtls"])
async def test_allowed_host_and_user_selected_same_origin_are_not_credential_grants(kind):
    calls = []
    http = HttpClient(
        transport=httpx.MockTransport(lambda request: calls.append(request)),
        allowed_hosts={"a2a.test"},
    )
    authentication = named_authentication(kind, http)
    value = payload("SendMessage", "https://a2a.test/a2a")
    value["agentCard"] = json_format.MessageToDict(peer_card("https://a2a.test/a2a"))
    value["endpoint"] = "https://a2a.test/a2a"
    with pytest.raises(ToolError) as error:
        await ProtocolServices(http).call("a2a", value, _authentication=authentication)
    assert error.value.details["reason"] == "unbound_card_credentials" and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bearer", "api_key"])
@pytest.mark.parametrize("authority", ["deployment-grant", "protected-tool-endpoint"])
async def test_exact_deployment_credential_grant_reaches_selected_sdk_peer(
    monkeypatch, kind, authority
):
    monkeypatch.setenv("A2A_TEST_SECRET", "selected-test-value")
    async with sdk_a2a_fixture() as (transport, calls, endpoint):
        http = client(transport, False)
        auth = named_authentication(kind, http)
        grants = {"selected": (endpoint,)} if authority == "deployment-grant" else {}
        protocols = ProtocolServices(http, a2a_credential_endpoints=grants)
        value = payload("SendMessage", "https://ignored-user-server.test/a2a")
        value["agentCard"] = json_format.MessageToDict(peer_card(endpoint))
        if authority == "protected-tool-endpoint":
            tools = ToolRegistry([ToolDefinition("peer", "a2a", endpoint, {}, auth)], protocols)
            result = await tools.invoke("peer", value)
        else:
            result = await protocols.call("a2a", value, _authentication=auth)
        assert result["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
        assert len(calls) == 1
        name = b"authorization" if kind == "bearer" else b"x-api-key"
        expected = b"Bearer selected-test-value" if kind == "bearer" else b"selected-test-value"
        assert calls[0]["headers"][name] == expected


@pytest.mark.asyncio
async def test_card_cannot_redirect_protected_tool_credentials_to_another_endpoint():
    calls = []
    http = HttpClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    auth = named_authentication("bearer", http)
    tools = ToolRegistry(
        [ToolDefinition("peer", "a2a", "https://a2a.test/authorized", {}, auth)],
        ProtocolServices(http),
    )
    value = payload("SendMessage", "https://a2a.test/a2a")
    value["agentCard"] = json_format.MessageToDict(peer_card("https://a2a.test/a2a"))
    with pytest.raises(ToolError) as error:
        await tools.invoke("peer", value)
    assert error.value.details["reason"] == "configured_endpoint_changed" and calls == []


@pytest.mark.asyncio
async def test_oauth_card_requires_exact_resource_endpoint_before_issuer_dispatch():
    calls = []
    http = HttpClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    auth = named_authentication("oauth", http)
    value = payload("SendMessage", "https://a2a.test/a2a")
    value["agentCard"] = json_format.MessageToDict(peer_card("https://a2a.test/a2a"))
    protocols = ProtocolServices(http, a2a_credential_endpoints={"selected": (value["server"],)})
    with pytest.raises(ToolError) as error:
        await protocols.call("a2a", value, _authentication=auth)
    assert error.value.details["reason"] == "unbound_card_credentials" and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["opaque-tls-context", "opaque-http-transport"])
async def test_public_discovery_rejects_unverifiable_identity_bearing_transport(binding):
    calls = []
    transport = httpx.AsyncHTTPTransport() if binding == "opaque-http-transport" else None
    http = HttpClient(
        transport=transport or httpx.MockTransport(lambda request: calls.append(request))
    )
    if binding == "opaque-tls-context":
        http.verify_tls = ssl.create_default_context()
    try:
        with pytest.raises(ToolError) as error:
            await fetch_validate_card(http, {"endpoint": "https://a2a.test/card"}, timeout=1)
        assert error.value.details["reason"] == "unsupported_card_transport" and calls == []
    finally:
        if transport is not None:
            await transport.aclose()


@pytest.mark.asyncio
async def test_card_business_dispatch_rejects_unbound_global_client_certificate():
    calls = []
    http = HttpClient(
        transport=httpx.MockTransport(lambda request: calls.append(request)),
        client_cert=("unavailable-business-cert", "unavailable-business-key"),
    )
    value = payload("SendMessage", "https://a2a.test/a2a")
    value["agentCard"] = json_format.MessageToDict(peer_card("https://a2a.test/a2a"))
    with pytest.raises(ToolError) as error:
        await ProtocolServices(http).call("a2a", value)
    assert error.value.details["reason"] == "unbound_card_credentials" and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GetTask", "CancelTask"])
async def test_correlated_valid_task_from_another_resource_is_uncertain(method):
    def foreign_task(response):
        response["result"]["id"] = "another-task"
        return response

    async with sdk_a2a_fixture(response_mutation=foreign_task) as (transport, calls, endpoint):
        with pytest.raises(_A2AOutcomeUncertain) as error:
            await ProtocolServices(client(transport, False)).call("a2a", payload(method, endpoint))
        assert error.value.details["reason"] == "response_identity_changed"
        assert len(calls) == 1 and calls[0]["body"]["params"]["id"] == "task-id"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["task", "message"])
@pytest.mark.parametrize("method", ["SendMessage", "SendStreamingMessage"])
async def test_existing_send_identity_is_preserved_in_task_and_message_alternatives(
    outcome, method
):
    async with sdk_a2a_fixture(outcome=outcome) as (transport, calls, endpoint):
        value = payload(method, endpoint)
        value["parameters"]["message"].update(taskId="existing-task", contextId="existing-context")
        result = await ProtocolServices(client(transport, False)).call("a2a", value)
        response = result[0] if method == "SendStreamingMessage" else result
        item = response[outcome]
        assert item["id" if outcome == "task" else "taskId"] == "existing-task"
        assert item["contextId"] == "existing-context" and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["task", "message"])
@pytest.mark.parametrize("field", ["task", "context"])
async def test_valid_send_reply_cannot_change_explicit_task_or_context(outcome, field):
    def foreign_identity(response):
        item = response["result"][outcome]
        name = ("id" if outcome == "task" else "taskId") if field == "task" else "contextId"
        item[name] = "foreign-valid-identity"
        return response

    async with sdk_a2a_fixture(outcome=outcome, response_mutation=foreign_identity) as (
        transport,
        calls,
        endpoint,
    ):
        value = payload("SendMessage", endpoint)
        value["parameters"]["message"].update(taskId="existing-task", contextId="existing-context")
        with pytest.raises(_A2AOutcomeUncertain) as error:
            await ProtocolServices(client(transport, False)).call("a2a", value)
        assert error.value.details["reason"] == "response_identity_changed" and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["task", "context"])
async def test_internally_consistent_official_stream_cannot_change_requested_send_identity(field):
    async def foreign_task(params, context):
        return a2a_types.Task(
            id="foreign-task" if field == "task" else params.message.task_id,
            context_id="foreign-context" if field == "context" else params.message.context_id,
            status=a2a_types.TaskStatus(state=a2a_types.TASK_STATE_COMPLETED),
        )

    async with sdk_a2a_fixture(send_handler=foreign_task) as (transport, calls, endpoint):
        value = payload("SendStreamingMessage", endpoint)
        value["parameters"]["message"].update(taskId="existing-task", contextId="existing-context")
        with pytest.raises(_A2AOutcomeUncertain) as error:
            await ProtocolServices(client(transport, False)).call("a2a", value)
        assert error.value.details["reason"] == "response_identity_changed" and len(calls) == 1


@pytest.mark.asyncio
async def test_subscribe_rejects_foreign_first_task_even_when_later_events_match_it():
    calls = []

    async def receiver(request):
        body = json.loads(request.content)
        calls.append(body)
        values = [
            {
                "task": {
                    "id": "foreign-task",
                    "contextId": "context-id",
                    "status": {"state": "TASK_STATE_WORKING"},
                }
            },
            {
                "statusUpdate": {
                    "taskId": "foreign-task",
                    "contextId": "context-id",
                    "status": {"state": "TASK_STATE_COMPLETED"},
                }
            },
        ]
        frames = "".join(
            "data: " + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": item}) + "\n\n"
            for item in values
        )
        return httpx.Response(
            200, content=frames.encode(), headers={"Content-Type": "text/event-stream"}
        )

    with pytest.raises(_A2AOutcomeUncertain) as error:
        await ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver))).call(
            "a2a", payload("SubscribeToTask", "https://a2a.test/a2a")
        )
    assert error.value.details["reason"] == "response_identity_changed" and len(calls) == 1
