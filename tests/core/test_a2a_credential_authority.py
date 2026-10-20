"""Card/profile cross-pairing must not turn discovery into credential authority."""

from __future__ import annotations

import json

import httpx
import pytest
from _a2a_sdk_fixture import peer_card
from google.protobuf import json_format
from open_workflow_agent.errors import ToolError
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile_name", "card_endpoint"),
    [
        ("one", "https://a2a.test/peer-two"),
        ("two", "https://a2a.test/peer-one"),
    ],
)
async def test_oauth_profile_cannot_be_cross_paired_with_another_same_origin_card(
    monkeypatch, profile_name, card_endpoint
):
    """Exact OAuth resource grants survive a broad deployment endpoint grant."""
    monkeypatch.setenv("A2A_CROSS_SECRET_ONE", "cross-secret-one")
    monkeypatch.setenv("A2A_CROSS_SECRET_TWO", "cross-secret-two")
    monkeypatch.setenv("A2A_CROSS_ID_ONE", "client-one")
    monkeypatch.setenv("A2A_CROSS_ID_TWO", "client-two")
    requests = []
    http = HttpClient(
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(500)
        )
    )
    endpoints = {
        "one": "https://a2a.test/peer-one",
        "two": "https://a2a.test/peer-two",
    }
    profiles = {
        "one": {
            "type": "oauth2_client_credentials",
            "token_url": "https://issuer.test/token-one",
            "client_id": {"from_env": "A2A_CROSS_ID_ONE"},
            "client_secret": {"from_env": "A2A_CROSS_SECRET_ONE"},
            "audience": "audience-one",
            "resource": "https://a2a.test/resource-one",
            "allowed_resource_endpoints": [endpoints["one"]],
        },
        "two": {
            "type": "oauth2_client_credentials",
            "token_url": "https://issuer.test/token-two",
            "client_id": {"from_env": "A2A_CROSS_ID_TWO"},
            "client_secret": {"from_env": "A2A_CROSS_SECRET_TWO"},
            "audience": "audience-two",
            "resource": "https://a2a.test/resource-two",
            "allowed_resource_endpoints": [endpoints["two"]],
        },
    }
    authentication = ProfileAuthentication(
        SecurityConfig.model_validate({"profiles": profiles}),
        profile_name,
        token_http=http,
    )
    card = json_format.MessageToDict(peer_card(card_endpoint))
    value = {
        "method": "SendMessage",
        "server": "https://ignored-user-server.test/a2a",
        "endpoint": card_endpoint,
        "agentCard": card,
        "parameters": {"message": {"parts": [{"text": "hello"}]}},
        "operation_id": "cross-profile-authority-check",
    }

    # Even a deployment-level grant for both peers cannot widen an OAuth
    # profile's independently configured resource/audience boundary.
    services = ProtocolServices(
        http,
        a2a_credential_endpoints={
            "one": tuple(endpoints.values()),
            "two": tuple(endpoints.values()),
        },
    )
    with pytest.raises(ToolError) as error:
        await services.call("a2a", value, _authentication=authentication)

    assert error.value.details["reason"] == "unbound_card_credentials"
    assert requests == []  # neither token issuer nor peer received a request
    rendered_error = json.dumps(error.value.as_dict())
    assert "cross-secret-one" not in rendered_error
    assert "cross-secret-two" not in rendered_error
