"""Secret-safety verification across the wired adapter paths.

SECURITY-3: the resolved credential value must never appear on any observable
surface — Agent Card, capabilities, A2A Task projections, protocol error
bodies, or configuration validation errors.
"""

from __future__ import annotations

import json

import httpx
import pytest
from open_workflow_agent.a2a import A2A_AGENT_CARD_PATH
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION as V1
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig
from open_workflow_agent.services import RuntimeServices
from pydantic import ValidationError

_SECRET = "partner-secret-value-0f9e2d"


def _security() -> dict[str, object]:
    return {
        "profiles": {
            "partner-agent": {
                "type": "bearer",
                "token": {"from_env": "OWA_SECRET_SAFETY_TOKEN"},
                "principal": "partner-client",
                "roles": ["partners"],
            }
        }
    }


def _config(**a2a: object) -> RuntimeConfig:
    return RuntimeConfig.model_validate({"a2a": {"enabled": True, **a2a}, "security": _security()})


def _contains(body: object, needle: str) -> bool:
    return needle in json.dumps(body)


@pytest.mark.asyncio
async def test_a2a_surfaces_never_expose_the_resolved_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OWA_SECRET_SAFETY_TOKEN", _SECRET)
    config = _config(
        security_profile="partner-agent",
        authorization={"rules": [{"actions": ["message.send"], "resources": ["skill:workflow"]}]},
    )
    services = RuntimeServices(
        config, model=FakeModel({"response": "a2a-reply"}), database_root=tmp_path
    )
    app = create_app(config=config, services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"a2a-version": V1, "Authorization": f"Bearer {_SECRET}"}

            card = await client.get(A2A_AGENT_CARD_PATH, headers=headers)
            assert card.status_code == 200
            assert not _contains(card.json(), _SECRET)

            capabilities = await client.get("/v1/capabilities")
            assert capabilities.status_code == 200
            assert not _contains(capabilities.json(), _SECRET)

            accepted = await client.post(
                "/a2a",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "SendMessage",
                    "params": {"message": {"role": "ROLE_USER", "parts": [{"text": "hi"}]}},
                },
            )
            assert accepted.status_code == 200
            task_id = accepted.json()["result"]["message"]["messageId"]
            assert not _contains(accepted.json(), _SECRET)

            projected = await client.post(
                "/a2a",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task_id}},
            )
            assert not _contains(projected.json(), _SECRET)

            missing = await client.post(
                "/a2a",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 3, "method": "GetTask", "params": {"id": "nope"}},
            )
            assert not _contains(missing.json(), _SECRET)

            unauthorized = await client.get(A2A_AGENT_CARD_PATH)
            assert unauthorized.status_code == 401
            assert not _contains(unauthorized.json(), _SECRET)

            forbidden = await client.post(
                "/a2a",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 4, "method": "CancelTask", "params": {"id": "x"}},
            )
            assert forbidden.status_code == 403
            assert not _contains(forbidden.json(), _SECRET)


def test_validation_errors_do_not_echo_secret_values() -> None:
    with pytest.raises(ValidationError) as raised:
        SecurityConfig.model_validate(
            {
                "profiles": {
                    "broken": {
                        "type": "bearer",
                        "token": {"from_env": "VALID_NAME", "value": _SECRET},
                    }
                }
            }
        )
    assert _SECRET not in str(raised.value)

    with pytest.raises(ValidationError) as inline:
        SecurityConfig.model_validate(
            {"profiles": {"broken": {"type": "bearer", "token": _SECRET}}}
        )
    assert _SECRET not in str(inline.value)


def test_profile_authentication_keeps_no_secret_state(monkeypatch) -> None:
    monkeypatch.setenv("OWA_SECRET_SAFETY_TOKEN", _SECRET)
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "partner-agent": {
                    "type": "bearer",
                    "token": {"from_env": "OWA_SECRET_SAFETY_TOKEN"},
                }
            }
        }
    )
    auth = ProfileAuthentication(security, "partner-agent")
    assert _SECRET not in repr(auth)
    assert _SECRET not in str(security)
    assert auth.headers("https://partner.test")["Authorization"] == f"Bearer {_SECRET}"
