"""Per-principal authorization enforcement for the bounded inbound A2A profile."""

from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.a2a import A2A_AGENT_CARD_PATH
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION as V1
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from pydantic import ValidationError

HEADERS = {"a2a-version": V1, "Authorization": "Bearer partner-token"}

_SECURITY = {
    "profiles": {
        "partner-agent": {
            "type": "bearer",
            "token": {"from_env": "OWA_TEST_A2A_BEARER"},
            "principal": "partner-client",
            "roles": ["partners"],
        }
    }
}


def _policy(rules: list[dict[str, object]]) -> dict[str, object]:
    return {
        "security_profile": "partner-agent",
        "authorization": {"rules": rules},
    }


def _config(
    *,
    security: dict[str, object] | None = _SECURITY,
    **a2a: object,
) -> RuntimeConfig:
    payload: dict[str, object] = {"a2a": {"enabled": True, **a2a}}
    if security is not None:
        payload["security"] = security
    return RuntimeConfig.model_validate(payload)


def _make_app(tmp_path, config: RuntimeConfig):
    services = RuntimeServices(
        config, model=FakeModel({"response": "a2a-reply"}), database_root=tmp_path
    )
    return create_app(config=config, services=services)


def _send() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]}},
    }


def test_authorization_without_authentication_is_rejected() -> None:
    rules = [{"actions": ["message.send"]}]
    with pytest.raises(ValidationError, match="requires a2a.security_profile"):
        _config(security=None, authorization={"rules": rules})

    with pytest.raises(ValidationError, match="unknown security profile"):
        _config(security_profile="missing", authorization={"rules": rules})


@pytest.mark.asyncio
async def test_policy_admission_for_implicit_workflow_skill(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OWA_TEST_A2A_BEARER", "partner-token")
    config = _config(
        **_policy(
            [
                {
                    "actions": ["message.send"],
                    "resources": ["skill:workflow"],
                    "roles": ["partners"],
                }
            ]
        )
    )
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            allowed = await client.post("/a2a", headers=HEADERS, json=_send())
            assert allowed.status_code == 200
            assert allowed.json()["result"]["message"]["parts"][0]["text"] == "a2a-reply"


@pytest.mark.asyncio
async def test_policy_denies_unlisted_action_and_resource(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OWA_TEST_A2A_BEARER", "partner-token")
    config = _config(
        **_policy([{"actions": ["tasks.get"], "resources": ["tasks"], "roles": ["partners"]}])
    )
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.post("/a2a", headers=HEADERS, json=_send())
            assert denied.status_code == 403
            assert denied.json()["error"]["message"] == "forbidden"


@pytest.mark.asyncio
async def test_policy_denies_principals_without_required_role(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OWA_TEST_A2A_BEARER", "partner-token")
    config = _config(
        **_policy(
            [
                {
                    "actions": ["message.send"],
                    "resources": ["skill:workflow"],
                    "roles": ["admins"],
                }
            ]
        )
    )
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.post("/a2a", headers=HEADERS, json=_send())
            assert denied.status_code == 403


@pytest.mark.asyncio
async def test_task_operations_are_authorized_separately(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OWA_TEST_A2A_BEARER", "partner-token")

    async def scenario(
        rules: list[dict[str, object]], method: str, params: dict[str, object]
    ) -> int:
        config = _config(**_policy(rules))
        app = _make_app(tmp_path / method, config)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/a2a",
                    headers=HEADERS,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                )
                return response.status_code

    assert (
        await scenario(
            [{"actions": ["message.send"], "resources": ["skill:workflow"]}],
            "GetTask",
            {"id": "missing"},
        )
        == 403
    )
    assert (
        await scenario(
            [{"actions": ["tasks.get", "tasks.cancel"], "resources": ["tasks"]}],
            "GetTask",
            {"id": "missing"},
        )
        == 200
    )
    assert (
        await scenario(
            [{"actions": ["tasks.cancel"], "resources": ["tasks"]}],
            "CancelTask",
            {"id": "missing"},
        )
        == 200
    )


@pytest.mark.asyncio
async def test_http_json_transport_returns_forbidden_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OWA_TEST_A2A_BEARER", "partner-token")
    config = _config(
        transport="http_json",
        **_policy([{"actions": ["tasks.get"], "resources": ["tasks"]}]),
    )
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.post(
                "/a2a/message:send",
                headers=HEADERS,
                json={"message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]}},
            )
            assert denied.status_code == 403
            assert denied.json()["error"]["code"] == "forbidden"
            card = await client.get(A2A_AGENT_CARD_PATH, headers=HEADERS)
            assert card.status_code == 200


@pytest.mark.asyncio
async def test_capabilities_advertise_authorization(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OWA_TEST_A2A_BEARER", "partner-token")
    config = _config(**_policy([{"actions": ["message.send"], "resources": ["skill:workflow"]}]))
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            capabilities = (await client.get("/v1/capabilities")).json()
            assert capabilities["features"]["a2a"]["authorization"] is True
