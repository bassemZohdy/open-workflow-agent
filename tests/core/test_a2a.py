from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.a2a import (
    A2A_AGENT_CARD_PATH,
    A2A_HTTP_JSON_MEDIA_TYPE,
    A2A_PROTOCOL_VERSION,
    A2A_SPEC_RELEASE,
    A2AConfig,
    JsonRpcError,
    _authenticate,
    _http_json_response,
    _jsonrpc_error,
    _jsonrpc_response,
    _permitted,
    _reply_message,
    _requested_protocol_version,
    _requested_skill_id,
    _task_id,
    build_agent_card,
    extract_message_text,
    extract_output_text,
)
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


def _app_config(*, security: dict[str, object] | None = None, **a2a: object) -> RuntimeConfig:
    payload: dict[str, object] = {"a2a": {"enabled": True, **a2a}}
    if security is not None:
        payload["security"] = security
    return RuntimeConfig.model_validate(payload)


def _make_app(tmp_path, config: RuntimeConfig):
    services = RuntimeServices(
        config, model=FakeModel({"response": "a2a-reply"}), database_root=tmp_path
    )
    return create_app(config=config, services=services)


def _v1_headers(**extra: str) -> dict[str, str]:
    return {"A2A-Version": A2A_PROTOCOL_VERSION, **extra}


@pytest.mark.asyncio
async def test_a2a_disabled_by_default(tmp_path) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    app = create_app(config=RuntimeConfig(), services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get(A2A_AGENT_CARD_PATH)).status_code == 404
            assert (await client.get("/.well-known/agent.json")).status_code == 404
            assert (await client.get("/a2a/agent.json")).status_code == 404
            assert (await client.post("/a2a", json={})).status_code == 404
            capabilities = (await client.get("/v1/capabilities")).json()
            assert capabilities["features"]["a2a"]["enabled"] is False


@pytest.mark.asyncio
async def test_agent_card_reports_bounded_v1_profile(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            card_response = await client.get(A2A_AGENT_CARD_PATH)
            assert card_response.status_code == 200
            card = card_response.json()
            assert card["name"] == "Open Workflow Agent"
            assert "protocolVersion" not in card
            assert "preferredTransport" not in card
            assert "additionalInterfaces" not in card
            assert card["supportedInterfaces"] == [
                {
                    "url": "http://test/a2a",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": A2A_PROTOCOL_VERSION,
                }
            ]
            assert card["capabilities"] == {"streaming": True, "pushNotifications": False}
            assert card["defaultInputModes"] == ["text/plain"]
            assert card["defaultOutputModes"] == ["text/plain"]
            assert (await client.get("/.well-known/agent.json")).status_code == 404
            assert (await client.get("/a2a/agent.json")).status_code == 404
            capabilities = (await client.get("/v1/capabilities")).json()
            assert capabilities["features"]["a2a"] == {
                "enabled": True,
                "specRelease": A2A_SPEC_RELEASE,
                "protocolVersion": A2A_PROTOCOL_VERSION,
                "transport": "jsonrpc",
                "card": A2A_AGENT_CARD_PATH,
                "streaming": True,
                "streamingOperations": ["SendStreamingMessage", "SubscribeToTask"],
                "pushNotifications": False,
                "tasks": True,
                "taskOperations": ["GetTask", "CancelTask"],
                "skills": [],
                "auth": None,
                "authorization": False,
            }


@pytest.mark.asyncio
async def test_jsonrpc_send_message_round_trip(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/a2a",
                headers=_v1_headers(),
                json={
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "role": "ROLE_USER",
                            "messageId": "m-1",
                            "parts": [{"text": "hello"}],
                        }
                    },
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["jsonrpc"] == "2.0"
            assert body["id"] == 7
            message = body["result"]["message"]
            assert message["role"] == "ROLE_AGENT"
            assert message["parts"] == [{"text": "a2a-reply"}]
            assert message["messageId"] == "a2a-m-1-reply"


@pytest.mark.asyncio
async def test_a2a_v1_version_is_required_and_wrong_version_is_rejected(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config())
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]}},
    }
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.post("/a2a", json=payload)
            assert missing.json()["error"]["code"] == -32009
            assert missing.json()["error"]["data"]["supportedVersion"] == "1.0"

            legacy = await client.post("/a2a", headers={"A2A-Version": "0.3"}, json=payload)
            assert legacy.json()["error"]["code"] == -32009

            query_version = await client.post("/a2a?A2A-Version=1.0", json=payload)
            assert query_version.status_code == 200


@pytest.mark.asyncio
async def test_jsonrpc_rejects_legacy_method_and_legacy_part_shape(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            legacy_method = await client.post(
                "/a2a",
                headers=_v1_headers(),
                json={"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {}},
            )
            assert legacy_method.json()["error"]["code"] == -32601

            legacy_part = await client.post(
                "/a2a",
                headers=_v1_headers(),
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "role": "ROLE_USER",
                            "parts": [{"kind": "text", "text": "hello"}],
                        }
                    },
                },
            )
            assert legacy_part.json()["error"]["code"] == -32602
            assert "legacy A2A part.kind" in legacy_part.json()["error"]["message"]


@pytest.mark.asyncio
async def test_http_json_transport_round_trip(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config(transport="http_json"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            card = (await client.get(A2A_AGENT_CARD_PATH)).json()
            assert card["supportedInterfaces"][0]["protocolBinding"] == "HTTP+JSON"
            response = await client.post(
                "/a2a/message:send",
                headers=_v1_headers(**{"Content-Type": A2A_HTTP_JSON_MEDIA_TYPE}),
                json={
                    "message": {
                        "role": "ROLE_USER",
                        "messageId": "m-2",
                        "parts": [{"text": "hello"}],
                    }
                },
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(A2A_HTTP_JSON_MEDIA_TYPE)
            message = response.json()["message"]
            assert message["role"] == "ROLE_AGENT"
            assert message["parts"] == [{"text": "a2a-reply"}]

            assert (await client.post("/a2a", json={})).status_code == 404


@pytest.mark.asyncio
async def test_http_json_rejects_unsupported_version(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config(transport="http_json"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/a2a/message:send",
                headers={"A2A-Version": "0.3"},
                json={"message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]}},
            )
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "version_not_supported"


_BEARER_SECURITY = {
    "profiles": {
        "partner-agent": {
            "type": "bearer",
            "token": {"from_env": "OWA_TEST_A2A_BEARER"},
        }
    }
}


@pytest.mark.asyncio
async def test_bearer_auth_is_enforced_when_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OWA_TEST_A2A_BEARER", "secret-token")
    app = _make_app(
        tmp_path,
        _app_config(security=_BEARER_SECURITY, security_profile="partner-agent"),
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get(A2A_AGENT_CARD_PATH)).status_code == 401
            assert (await client.post("/a2a", json={})).status_code == 401
            authorized = await client.get(
                A2A_AGENT_CARD_PATH, headers={"Authorization": "Bearer secret-token"}
            )
            assert authorized.status_code == 200
            capabilities = (await client.get("/v1/capabilities")).json()
            assert capabilities["features"]["a2a"]["auth"] == "bearer"


@pytest.mark.asyncio
async def test_bearer_auth_fails_closed_when_secret_is_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OWA_TEST_A2A_BEARER", raising=False)
    app = _make_app(
        tmp_path,
        _app_config(security=_BEARER_SECURITY, security_profile="partner-agent"),
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                A2A_AGENT_CARD_PATH, headers={"Authorization": "Bearer secret-token"}
            )
            assert response.status_code == 401


@pytest.mark.asyncio
async def test_oversized_message_is_rejected(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config(max_message_chars=10))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/a2a",
                headers=_v1_headers(),
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "SendMessage",
                    "params": {"message": {"role": "ROLE_USER", "parts": [{"text": "x" * 11}]}},
                },
            )
            assert response.status_code == 200
            assert response.json()["error"]["code"] == -32602


def test_agent_card_build_is_transport_aware() -> None:
    config = A2AConfig(transport="http_json")
    card = build_agent_card(config, url="http://test/a2a", workflow_name="demo")
    assert card["supportedInterfaces"] == [
        {
            "url": "http://test/a2a",
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }
    ]
    assert card["skills"][0]["name"] == "demo"


@pytest.mark.asyncio
async def test_agent_card_honors_public_base_url(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config(public_base_url="https://agents.example.com"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://internal:8080"
        ) as client:
            card = (await client.get(A2A_AGENT_CARD_PATH)).json()
            assert card["supportedInterfaces"][0]["url"] == "https://agents.example.com/a2a"


def test_invalid_public_base_url_is_rejected() -> None:
    with pytest.raises(Exception, match="public_base_url"):
        RuntimeConfig.model_validate(
            {"a2a": {"enabled": True, "public_base_url": "agents.example.com"}}
        )


def test_unknown_transport_is_rejected() -> None:
    with pytest.raises(Exception, match="transport"):
        RuntimeConfig.model_validate({"a2a": {"enabled": True, "transport": "carrier-pigeon"}})


def test_a2a_text_output_and_response_helpers():
    assert extract_message_text([{"text": "hello"}, {"text": "world"}]) == "hello\nworld"
    with pytest.raises(ValueError, match="non-empty array"):
        extract_message_text([])
    with pytest.raises(ValueError, match="entries must be objects"):
        extract_message_text(["hello"])
    with pytest.raises(ValueError, match="non-empty text"):
        extract_message_text([{"text": ""}])
    with pytest.raises(ValueError, match="exceeds"):
        extract_message_text([{"text": "x"}] * 65)

    assert extract_output_text("reply") == "reply"
    assert extract_output_text({"response": {"text": "nested"}}) == "nested"
    assert extract_output_text({"output": {"message": "nested"}}) == "nested"
    assert extract_output_text(None) == ""
    assert extract_output_text({"value": 1}) == '{"value": 1}'
    assert _reply_message({}, "ok")["messageId"] == "a2a-reply"
    assert _reply_message({"messageId": "m-1"}, "ok")["messageId"] == "a2a-m-1-reply"

    response = _jsonrpc_response(1, {"ok": True})
    assert response.status_code == 200
    assert httpx.Response(200, content=response.body).json()["result"] == {"ok": True}
    error = _jsonrpc_error(1, JsonRpcError(-1, "bad", details={"safe": True}, http_status=422))
    assert httpx.Response(error.status_code, content=error.body).json()["error"]["data"] == {
        "safe": True
    }
    http_response = _http_json_response({"ok": True}, status_code=201)
    assert http_response.status_code == 201
    assert http_response.media_type == A2A_HTTP_JSON_MEDIA_TYPE


def test_a2a_request_selection_and_authentication_helpers(monkeypatch):
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "headers": [(b"a2a-version", b"1.0")],
            "query_string": b"A2A-Version=0.3",
            "method": "GET",
            "path": "/a2a",
            "raw_path": b"/a2a",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "root_path": "",
        }
    )
    assert _requested_protocol_version(request) == "1.0"
    assert _requested_skill_id({"skillId": " from-params "}, {}) == "from-params"
    assert _requested_skill_id({}, {"metadata": {"skillId": "from-message"}}) == "from-message"
    assert _requested_skill_id({}, {}) is None
    with pytest.raises(JsonRpcError, match="params.id"):
        _task_id({})
    assert _task_id({"id": "task-1"}) == "task-1"
    assert _permitted(
        object(),
        None,
        action="message.send",
        resource="skill:workflow",  # type: ignore[arg-type]
    )

    monkeypatch.setenv("OWA_A2A_HELPER_TOKEN", "secret")
    security = RuntimeConfig.model_validate(
        {
            "security": {
                "profiles": {
                    "partner": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_A2A_HELPER_TOKEN"},
                    }
                }
            }
        }
    ).security
    config = A2AConfig(security_profile="partner")
    authorized = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer secret")],
            "method": "GET",
            "path": "/a2a",
            "raw_path": b"/a2a",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "query_string": b"",
            "root_path": "",
        }
    )
    assert _authenticate(config, security, authorized) is not None
    unauthorized = Request(
        {
            **authorized.scope,
            "headers": [(b"authorization", b"Bearer wrong")],
        }
    )
    assert _authenticate(config, security, unauthorized) is None
    assert _authenticate(A2AConfig(), security, unauthorized).identity == "anonymous"
