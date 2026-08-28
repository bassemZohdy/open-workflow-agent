from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.a2a import (
    A2A_AGENT_CARD_PATH,
    A2A_HTTP_JSON_MEDIA_TYPE,
    A2A_PROTOCOL_VERSION,
    A2A_SPEC_RELEASE,
    A2AConfig,
    build_agent_card,
)
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


def _app_config(**a2a: object) -> RuntimeConfig:
    return RuntimeConfig.model_validate({"a2a": {"enabled": True, **a2a}})


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
            assert card["capabilities"] == {"streaming": False, "pushNotifications": False}
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
                "streaming": False,
                "pushNotifications": False,
                "tasks": True,
                "taskOperations": ["GetTask", "CancelTask"],
                "auth": None,
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


@pytest.mark.asyncio
async def test_bearer_auth_is_enforced_when_configured(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config(auth_token="secret-token"))
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
