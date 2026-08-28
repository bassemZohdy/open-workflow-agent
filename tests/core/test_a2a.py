from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.a2a import A2AConfig, build_agent_card
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


@pytest.mark.asyncio
async def test_a2a_disabled_by_default(tmp_path) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    app = create_app(config=RuntimeConfig(), services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/a2a/agent.json")).status_code == 404
            assert (await client.post("/a2a", json={})).status_code == 404
            capabilities = (await client.get("/v1/capabilities")).json()
            assert capabilities["features"]["a2a"]["enabled"] is False


@pytest.mark.asyncio
async def test_agent_card_reports_bounded_profile(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            card_response = await client.get("/a2a/agent.json")
            assert card_response.status_code == 200
            card = card_response.json()
            assert card["name"] == "Open Workflow Agent"
            assert card["preferredTransport"] == "JSONRPC"
            assert card["capabilities"] == {"streaming": False, "pushNotifications": False}
            assert card["url"].endswith("/a2a")
            well_known = await client.get("/.well-known/agent.json")
            assert well_known.json()["preferredTransport"] == "JSONRPC"
            capabilities = (await client.get("/v1/capabilities")).json()
            assert capabilities["features"]["a2a"] == {
                "enabled": True,
                "transport": "jsonrpc",
                "card": "/a2a/agent.json",
                "streaming": False,
                "pushNotifications": False,
                "auth": None,
            }


@pytest.mark.asyncio
async def test_jsonrpc_message_send_round_trip(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "message/send",
                    "params": {
                        "message": {
                            "role": "user",
                            "messageId": "m-1",
                            "parts": [{"kind": "text", "text": "hello"}],
                        }
                    },
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["jsonrpc"] == "2.0"
            assert body["id"] == 7
            result = body["result"]
            assert result["kind"] == "message"
            assert result["role"] == "agent"
            assert result["parts"][0]["text"] == "a2a-reply"


@pytest.mark.asyncio
async def test_jsonrpc_rejects_unknown_method_and_bad_parts(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unknown = await client.post(
                "/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/send", "params": {}}
            )
            assert unknown.json()["error"]["code"] == -32601
            bad_parts = await client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "message/send",
                    "params": {"message": {"parts": []}},
                },
            )
            assert bad_parts.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_http_json_transport_round_trip(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config(transport="http_json"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            card = (await client.get("/a2a/agent.json")).json()
            assert card["preferredTransport"] == "HTTP+JSON"
            response = await client.post(
                "/a2a",
                json={
                    "role": "user",
                    "messageId": "m-2",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
            )
            assert response.status_code == 200
            message = response.json()
            assert message["role"] == "agent"
            assert message["parts"][0]["text"] == "a2a-reply"


@pytest.mark.asyncio
async def test_bearer_auth_is_enforced_when_configured(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config(auth_token="secret-token"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/a2a/agent.json")).status_code == 401
            assert (await client.post("/a2a", json={})).status_code == 401
            authorized = await client.get(
                "/a2a/agent.json", headers={"Authorization": "Bearer secret-token"}
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
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "message/send",
                    "params": {
                        "message": {"role": "user", "parts": [{"kind": "text", "text": "x" * 11}]}
                    },
                },
            )
            assert response.status_code == 200
            assert response.json()["error"]["code"] == -32602


def test_agent_card_build_is_transport_aware() -> None:
    config = A2AConfig(transport="http_json")
    card = build_agent_card(config, url="http://test/a2a", workflow_name="demo")
    assert card["preferredTransport"] == "HTTP+JSON"
    assert card["skills"][0]["name"] == "demo"


@pytest.mark.asyncio
async def test_agent_card_honors_public_base_url(tmp_path) -> None:
    app = _make_app(tmp_path, _app_config(public_base_url="https://agents.example.com"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://internal:8080"
        ) as client:
            card = (await client.get("/a2a/agent.json")).json()
            assert card["url"] == "https://agents.example.com/a2a"


def test_invalid_public_base_url_is_rejected() -> None:
    with pytest.raises(Exception, match="public_base_url"):
        RuntimeConfig.model_validate(
            {"a2a": {"enabled": True, "public_base_url": "agents.example.com"}}
        )


def test_unknown_transport_is_rejected() -> None:
    with pytest.raises(Exception, match="transport"):
        RuntimeConfig.model_validate({"a2a": {"enabled": True, "transport": "carrier-pigeon"}})
