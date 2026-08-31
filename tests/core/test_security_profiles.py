from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.config import RuntimeConfig, ToolConfig
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.security import (
    ProfileAuthentication,
    SecurityConfig,
    resolve_secret,
)
from open_workflow_agent.tools import ToolRegistry


def _security(
    token_env: str = "OWA_PROFILE_TOKEN", token: str = "profile-secret"
) -> SecurityConfig:
    import os

    os.environ[token_env] = token
    return SecurityConfig.model_validate(
        {"profiles": {"partner": {"type": "bearer", "token": {"from_env": token_env}}}}
    )


def test_profile_authentication_resolves_bearer_and_api_key() -> None:
    security = _security()
    auth = ProfileAuthentication(security, "partner")
    assert auth.headers("https://catalog.test") == {"Authorization": "Bearer profile-secret"}

    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "partner": {
                    "type": "api_key",
                    "key": {"from_env": "OWA_PROFILE_TOKEN"},
                    "header": "X-Partner-Key",
                }
            }
        }
    )
    auth = ProfileAuthentication(security, "partner")
    assert auth.headers("https://catalog.test") == {"X-Partner-Key": "profile-secret"}


def test_profile_authentication_fails_closed_on_unknown_profile_and_missing_secret() -> None:
    security = _security()
    with pytest.raises(ValueError, match="unknown security profile"):
        ProfileAuthentication(security, "missing")

    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "partner": {
                    "type": "bearer",
                    "token": {"from_env": "OWA_UNSET_PROFILE_TOKEN"},
                }
            }
        }
    )
    auth = ProfileAuthentication(security, "partner")
    with pytest.raises(ValueError, match="OWA_UNSET_PROFILE_TOKEN"):
        auth.headers("https://catalog.test")


def test_runtime_config_rejects_unknown_and_non_header_profile_references() -> None:
    base_security = {
        "security": {
            "profiles": {
                "partner": {"type": "bearer", "token": {"from_env": "OWA_PROFILE_TOKEN"}},
                "mtls-only": {
                    "type": "mtls",
                    "certificate": {"from_env": "CERT"},
                    "private_key": {"from_env": "KEY"},
                },
            }
        }
    }
    with pytest.raises(ValueError, match="unknown security profile"):
        RuntimeConfig.model_validate(
            {
                **base_security,
                "approvals": {"enabled": True, "operator_security_profile": "missing"},
            }
        )
    with pytest.raises(ValueError, match="must reference a security profile of type"):
        RuntimeConfig.model_validate(
            {
                **base_security,
                "approvals": {"enabled": True, "operator_security_profile": "mtls-only"},
            }
        )
    with pytest.raises(ValueError, match="tools\\[0\\]"):
        RuntimeConfig.model_validate(
            {
                **base_security,
                "tools": [
                    {
                        "type": "openapi",
                        "endpoint": "https://example.test",
                        "security_profile": "missing",
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_tool_security_profile_injects_bearer_header() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    security = _security()
    protocols = ProtocolServices(
        http=HttpClient(
            transport=httpx.MockTransport(handler),
            allowed_hosts={"example.test"},
        )
    )
    registry = ToolRegistry.from_config(
        [
            ToolConfig.model_validate(
                {
                    "type": "openapi",
                    "name": "partner-api",
                    "endpoint": "https://example.test/call",
                    "security_profile": "partner",
                }
            )
        ],
        protocols,
        security=security,
    )
    await registry.invoke(
        "partner-api",
        {"method": "GET", "operationId": "get-status", "parameters": {}},
    )
    assert captured["authorization"] == "Bearer profile-secret"


@pytest.mark.asyncio
async def test_tool_with_unknown_profile_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown security profile"):
        ToolRegistry.from_config(
            [
                ToolConfig.model_validate(
                    {
                        "type": "openapi",
                        "name": "partner-api",
                        "endpoint": "https://example.test/call",
                        "security_profile": "missing",
                    }
                )
            ],
            ProtocolServices(),
            security=_security(),
        )


def test_resolve_secret_never_returns_empty_values() -> None:
    import os

    from open_workflow_agent.security import SecretReference

    os.environ.pop("OWA_EMPTY_SECRET_TEST", None)
    with pytest.raises(ValueError, match="OWA_EMPTY_SECRET_TEST"):
        resolve_secret(SecretReference(from_env="OWA_EMPTY_SECRET_TEST"))
    os.environ["OWA_EMPTY_SECRET_TEST"] = ""
    with pytest.raises(ValueError, match="OWA_EMPTY_SECRET_TEST"):
        resolve_secret(SecretReference(from_env="OWA_EMPTY_SECRET_TEST"))
