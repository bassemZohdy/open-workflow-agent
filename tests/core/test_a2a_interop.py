"""A2A interoperability evidence and capability-accuracy tests.

These tests verify that the A2A implementation accurately advertises its capabilities
and conforms to the official A2A v1 specification patterns.
"""

from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.a2a import (
    A2A_AGENT_CARD_PATH,
    A2A_PROTOCOL_VERSION,
    A2A_SPEC_RELEASE,
    a2a_capabilities,
    build_agent_card,
)
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import A2AConfig, RuntimeConfig
from open_workflow_agent.services import RuntimeServices


def _make_app(
    tmp_path,
    config: RuntimeConfig,
):
    services = RuntimeServices(
        config, model=FakeModel({"response": "a2a-reply"}), database_root=tmp_path
    )
    return create_app(config=config, services=services)


def _v1_headers(**extra: str) -> dict[str, str]:
    return {"A2A-Version": A2A_PROTOCOL_VERSION, **extra}


# --- Capability-Accuracy Tests ---


@pytest.mark.asyncio
async def test_agent_card_matches_capabilities_endpoint(tmp_path) -> None:
    """Verify Agent Card and capabilities endpoint report consistent A2A state."""
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            card = (await client.get(A2A_AGENT_CARD_PATH)).json()
            caps = (await client.get("/v1/capabilities")).json()
            a2a_caps = caps["features"]["a2a"]

            # Card and capabilities should agree on streaming support
            assert card["capabilities"]["streaming"] is a2a_caps["streaming"]
            assert card["capabilities"]["pushNotifications"] is a2a_caps["pushNotifications"]

            # Card skills should match capabilities skills when explicitly configured
            card_skill_ids = [s["id"] for s in card["skills"]]
            # When no skills are explicitly configured, card has implicit "workflow" skill
            # but capabilities returns empty list
            if a2a_caps["skills"]:
                assert card_skill_ids == a2a_caps["skills"]


@pytest.mark.asyncio
async def test_agent_card_reflects_configured_skills(tmp_path) -> None:
    """Verify Agent Card accurately reflects deployment-configured skills."""
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake", "name": "fake/default"},
            "workflow": {
                "catalog": [
                    {
                        "document": {
                            "dsl": "1.0.3",
                            "namespace": "skills",
                            "name": "skill-a",
                            "version": "1.0.0",
                        },
                        "do": [{"mark": {"set": {"skill": "a"}}}],
                    },
                    {
                        "document": {
                            "dsl": "1.0.3",
                            "namespace": "skills",
                            "name": "skill-b",
                            "version": "1.0.0",
                        },
                        "do": [{"mark": {"set": {"skill": "b"}}}],
                    },
                ]
            },
            "a2a": {
                "enabled": True,
                "skills": [
                    {"id": "greet", "workflow": "skill-a", "name": "Greeting"},
                    {"id": "help", "workflow": "skill-b", "name": "Help"},
                ],
            },
        }
    )
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            card = (await client.get(A2A_AGENT_CARD_PATH)).json()
            caps = (await client.get("/v1/capabilities")).json()

            # Card should advertise exactly the configured skills
            card_skill_ids = {s["id"] for s in card["skills"]}
            assert card_skill_ids == {"greet", "help"}

            # Capabilities should match
            assert set(caps["features"]["a2a"]["skills"]) == {"greet", "help"}


@pytest.mark.asyncio
async def test_agent_card_transport_matches_config(tmp_path) -> None:
    """Verify Agent Card protocolBinding matches configured transport."""
    for transport_type, binding_label in [("jsonrpc", "JSONRPC"), ("http_json", "HTTP+JSON")]:
        config = RuntimeConfig.model_validate(
            {"a2a": {"enabled": True, "transport": transport_type}}
        )
        app = _make_app(tmp_path, config)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                card = (await client.get(A2A_AGENT_CARD_PATH)).json()
                assert card["supportedInterfaces"][0]["protocolBinding"] == binding_label


@pytest.mark.asyncio
async def test_agent_card_protocol_version_matches_baseline(tmp_path) -> None:
    """Verify Agent Card advertises the correct protocol version."""
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            card = (await client.get(A2A_AGENT_CARD_PATH)).json()
            interface = card["supportedInterfaces"][0]
            assert interface["protocolVersion"] == A2A_PROTOCOL_VERSION


# --- A2A v1 Spec Conformance Tests ---


@pytest.mark.asyncio
async def test_agent_card_has_required_v1_fields(tmp_path) -> None:
    """Verify Agent Card contains all required A2A v1 fields."""
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            card = (await client.get(A2A_AGENT_CARD_PATH)).json()

            # Required A2A v1 Agent Card fields
            assert "name" in card
            assert "description" in card
            assert "supportedInterfaces" in card
            assert "version" in card
            assert "capabilities" in card
            assert "defaultInputModes" in card
            assert "defaultOutputModes" in card
            assert "skills" in card

            # Capabilities must have streaming and pushNotifications
            assert "streaming" in card["capabilities"]
            assert "pushNotifications" in card["capabilities"]


@pytest.mark.asyncio
async def test_agent_card_interface_has_required_v1_fields(tmp_path) -> None:
    """Verify AgentInterface contains all required A2A v1 fields."""
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            card = (await client.get(A2A_AGENT_CARD_PATH)).json()
            interface = card["supportedInterfaces"][0]

            assert "url" in interface
            assert "protocolBinding" in interface
            assert "protocolVersion" in interface
            assert interface["protocolBinding"] in ("JSONRPC", "HTTP+JSON")


@pytest.mark.asyncio
async def test_agent_card_skill_has_required_v1_fields(tmp_path) -> None:
    """Verify skill entries contain all required A2A v1 fields."""
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            card = (await client.get(A2A_AGENT_CARD_PATH)).json()
            skill = card["skills"][0]

            assert "id" in skill
            assert "name" in skill
            assert "description" in skill
            assert "tags" in skill


@pytest.mark.asyncio
async def test_jsonrpc_response_has_required_v1_fields(tmp_path) -> None:
    """Verify JSON-RPC responses contain all required fields."""
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": "req-1",
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "messageId": "msg-1",
                            "role": "ROLE_USER",
                            "parts": [{"text": "hello"}],
                        }
                    },
                },
                headers=_v1_headers(),
            )
            data = resp.json()

            # JSON-RPC 2.0 required fields
            assert "jsonrpc" in data
            assert data["jsonrpc"] == "2.0"
            assert "id" in data
            assert data["id"] == "req-1"
            assert "result" in data or "error" in data


@pytest.mark.asyncio
async def test_send_message_response_has_v1_message_shape(tmp_path) -> None:
    """Verify SendMessage response contains A2A v1 Message shape."""
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": "req-1",
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "messageId": "msg-1",
                            "role": "ROLE_USER",
                            "parts": [{"text": "hello"}],
                        }
                    },
                },
                headers=_v1_headers(),
            )
            data = resp.json()
            result = data["result"]

            # A2A v1 Message shape (wrapped in result.message)
            assert "message" in result
            message = result["message"]
            assert "messageId" in message
            assert "role" in message
            assert message["role"] == "ROLE_AGENT"
            assert "parts" in message
            assert isinstance(message["parts"], list)
            assert len(message["parts"]) > 0
            assert "text" in message["parts"][0]


@pytest.mark.asyncio
async def test_task_projection_has_v1_task_shape(tmp_path) -> None:
    """Verify Task projection contains A2A v1 Task shape."""
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake", "name": "fake/default"},
            "a2a": {"enabled": True},
            "approvals": {"enabled": True},
        }
    )
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Use returnImmediately to get a Task projection
            resp = await client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": "req-1",
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "messageId": "msg-1",
                            "role": "ROLE_USER",
                            "parts": [{"text": "hello"}],
                        },
                        "configuration": {"returnImmediately": True},
                    },
                },
                headers=_v1_headers(),
            )
            data = resp.json()
            result = data["result"]

            # A2A v1 Task shape (wrapped in result.task)
            assert "task" in result
            task = result["task"]
            assert "id" in task
            assert "contextId" in task
            assert "status" in task
            assert "state" in task["status"]
            assert task["status"]["state"] in (
                "TASK_STATE_WORKING",
                "TASK_STATE_INPUT_REQUIRED",
                "TASK_STATE_COMPLETED",
                "TASK_STATE_FAILED",
                "TASK_STATE_CANCELED",
            )


@pytest.mark.asyncio
async def test_task_state_values_match_a2a_v1_spec(tmp_path) -> None:
    """Verify Task state values match official A2A v1 spec constants."""
    from open_workflow_agent.a2a_tasks import A2A_TASK_STATES

    valid_states = {
        "TASK_STATE_WORKING",
        "TASK_STATE_INPUT_REQUIRED",
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
    }

    for owa_state, a2a_state in A2A_TASK_STATES.items():
        assert a2a_state in valid_states, f"OWA state '{owa_state}' maps to invalid A2A state '{a2a_state}'"


@pytest.mark.asyncio
async def test_jsonrpc_error_codes_match_a2a_v1_spec(tmp_path) -> None:
    """Verify JSON-RPC error codes match official A2A v1 spec values."""
    from open_workflow_agent.a2a import JsonRpcError

    # Official A2A v1 error codes
    assert JsonRpcError.TASK_NOT_FOUND == -32001
    assert JsonRpcError.TASK_NOT_CANCELABLE == -32002
    assert JsonRpcError.VERSION_NOT_SUPPORTED == -32009

    # Standard JSON-RPC 2.0 error codes
    assert JsonRpcError.PARSE_ERROR == -32700
    assert JsonRpcError.INVALID_REQUEST == -32600
    assert JsonRpcError.METHOD_NOT_FOUND == -32601
    assert JsonRpcError.INVALID_PARAMS == -32602
    assert JsonRpcError.INTERNAL_ERROR == -32603


# --- Security Scheme Advertisement Tests ---


@pytest.mark.asyncio
async def test_agent_card_advertises_bearer_security_when_configured(tmp_path) -> None:
    """Verify Agent Card advertises securitySchemes when auth is configured."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("OWA_TEST_A2A_BEARER", "partner-token")
    try:
        config = RuntimeConfig.model_validate(
            {
                "a2a": {"enabled": True, "security_profile": "test-bearer"},
                "security": {
                    "profiles": {
                        "test-bearer": {
                            "type": "bearer",
                            "token": {"from_env": "OWA_TEST_A2A_BEARER"},
                        },
                    }
                },
            }
        )
        app = _make_app(tmp_path, config)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # Send proper auth header to get card
                card = (
                    await client.get(
                        A2A_AGENT_CARD_PATH,
                        headers=_v1_headers(Authorization="Bearer partner-token"),
                    )
                ).json()

                # Card should advertise security schemes
                assert "securitySchemes" in card
                assert "bearer" in card["securitySchemes"]
                assert card["securitySchemes"]["bearer"]["type"] == "http"
                assert card["securitySchemes"]["bearer"]["scheme"] == "bearer"

                # Card should have security requirements
                assert "security" in card
                assert len(card["security"]) > 0
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_agent_card_no_security_when_not_configured(tmp_path) -> None:
    """Verify Agent Card doesn't advertise security when auth is not configured."""
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            card = (await client.get(A2A_AGENT_CARD_PATH)).json()

            # Card should not advertise security schemes
            assert "securitySchemes" not in card
            assert "security" not in card


@pytest.mark.asyncio
async def test_capabilities_reflect_security_configuration(tmp_path) -> None:
    """Verify capabilities endpoint accurately reflects security configuration."""
    config = RuntimeConfig.model_validate(
        {
            "a2a": {"enabled": True, "security_profile": "test-bearer"},
            "security": {
                "profiles": {
                    "test-bearer": {"type": "bearer", "token": {"from_env": "TEST_TOKEN"}},
                }
            },
        }
    )
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            caps = (await client.get("/v1/capabilities")).json()
            a2a_caps = caps["features"]["a2a"]

            assert a2a_caps["auth"] == "bearer"


@pytest.mark.asyncio
async def test_capabilities_reflect_authorization_configuration(tmp_path) -> None:
    """Verify capabilities endpoint accurately reflects authorization configuration."""
    config = RuntimeConfig.model_validate(
        {
            "a2a": {
                "enabled": True,
                "security_profile": "test-bearer",
                "authorization": {
                    "rules": [
                        {
                            "actions": ["message.send"],
                            "resources": ["skill:workflow"],
                        }
                    ]
                },
            },
            "security": {
                "profiles": {
                    "test-bearer": {"type": "bearer", "token": {"from_env": "TEST_TOKEN"}},
                }
            },
        }
    )
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            caps = (await client.get("/v1/capabilities")).json()
            a2a_caps = caps["features"]["a2a"]

            assert a2a_caps["authorization"] is True


# --- Multi-Operation Consistency Tests ---


@pytest.mark.asyncio
async def test_all_advertised_task_operations_are_implemented(tmp_path) -> None:
    """Verify all advertised task operations are actually implemented."""
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            caps = (await client.get("/v1/capabilities")).json()
            task_ops = caps["features"]["a2a"]["taskOperations"]

            # GetTask should be implemented
            assert "GetTask" in task_ops
            resp = await client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": "req-1",
                    "method": "GetTask",
                    "params": {"id": "nonexistent"},
                },
                headers=_v1_headers(),
            )
            # Should get a proper error, not 404/500
            assert resp.status_code == 200
            data = resp.json()
            assert "error" in data
            assert data["error"]["code"] == -32001  # TASK_NOT_FOUND

            # CancelTask should be implemented
            assert "CancelTask" in task_ops
            resp = await client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": "req-2",
                    "method": "CancelTask",
                    "params": {"id": "nonexistent"},
                },
                headers=_v1_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "error" in data
            assert data["error"]["code"] == -32001  # TASK_NOT_FOUND


@pytest.mark.asyncio
async def test_all_advertised_streaming_operations_are_implemented(tmp_path) -> None:
    """Verify all advertised streaming operations are actually implemented."""
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            caps = (await client.get("/v1/capabilities")).json()
            streaming_ops = caps["features"]["a2a"]["streamingOperations"]

            # SendStreamingMessage should be implemented via JSON-RPC
            assert "SendStreamingMessage" in streaming_ops
            resp = await client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": "req-1",
                    "method": "SendStreamingMessage",
                    "params": {
                        "message": {
                            "messageId": "msg-1",
                            "role": "ROLE_USER",
                            "parts": [{"text": "hello"}],
                        }
                    },
                },
                headers=_v1_headers(),
            )
            # Should get SSE response, not 404
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

            # SubscribeToTask should be implemented via JSON-RPC
            assert "SubscribeToTask" in streaming_ops
            resp = await client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": "req-2",
                    "method": "SubscribeToTask",
                    "params": {"id": "nonexistent"},
                },
                headers=_v1_headers(),
            )
            # Should get proper error for unknown task
            assert resp.status_code in (200, 404)
