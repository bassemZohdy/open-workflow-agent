"""Tests for OAuth2 client-credentials and mTLS security profile support."""

from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.errors import ToolError
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.security import (
    ProfileAuthentication,
    SecurityConfig,
)


def _oauth_security() -> SecurityConfig:
    return SecurityConfig.model_validate(
        {
            "profiles": {
                "oauth": {
                    "type": "oauth2_client_credentials",
                    "token_url": "https://auth.example.com/token",
                    "client_id": {"from_env": "CLIENT_ID"},
                    "client_secret": {"from_env": "CLIENT_SECRET"},
                    "allowed_resource_endpoints": ["https://api.example.com"],
                }
            }
        }
    )


def test_oauth2_profile_accepted_by_profile_authentication() -> None:
    """Verify OAuth2 client-credentials profiles are accepted."""
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "oauth": {
                    "type": "oauth2_client_credentials",
                    "token_url": "https://auth.example.com/token",
                    "client_id": {"from_env": "CLIENT_ID"},
                    "client_secret": {"from_env": "CLIENT_SECRET"},
                }
            }
        }
    )
    auth = ProfileAuthentication(security, "oauth")
    assert auth._supports_oauth2 is True


def test_mtls_profile_accepted_by_profile_authentication() -> None:
    """Verify mTLS profiles are accepted."""
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "mtls": {
                    "type": "mtls",
                    "certificate": {"from_env": "CERT"},
                    "private_key": {"from_env": "KEY"},
                }
            }
        }
    )
    auth = ProfileAuthentication(security, "mtls")
    assert auth._supports_mtls is True


@pytest.mark.asyncio
async def test_oauth2_headers_returns_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIENT_ID", "test-client")
    monkeypatch.setenv("CLIENT_SECRET", "test-secret")
    security = _oauth_security()
    auth = ProfileAuthentication(
        security,
        "oauth",
        token_http=HttpClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "access_token": "test-token-123",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            )
        ),
    )
    binding = await auth.prepare("https://api.example.com", remaining_timeout=1)
    assert binding.headers == {"Authorization": "Bearer test-token-123"}


@pytest.mark.asyncio
async def test_oauth2_token_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIENT_ID", "test-client")
    monkeypatch.setenv("CLIENT_SECRET", "test-secret")
    calls = []

    def issuer(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": f"token-{len(calls)}",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    auth = ProfileAuthentication(
        _oauth_security(), "oauth", token_http=HttpClient(transport=httpx.MockTransport(issuer))
    )
    first = await auth.prepare("https://api.example.com", remaining_timeout=1)
    second = await auth.prepare("https://api.example.com", remaining_timeout=1)
    assert first.headers == second.headers == {"Authorization": "Bearer token-1"}
    assert len(calls) == 1


def test_mtls_client_cert_returns_certificate_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify mTLS profile returns certificate and key."""
    monkeypatch.setenv(
        "CERT_CONTENT", "-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----"
    )
    monkeypatch.setenv(
        "KEY_CONTENT", "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----"
    )

    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "mtls": {
                    "type": "mtls",
                    "certificate": {"from_env": "CERT_CONTENT"},
                    "private_key": {"from_env": "KEY_CONTENT"},
                }
            }
        }
    )

    auth = ProfileAuthentication(security, "mtls")
    cert = auth.client_cert()

    assert cert is not None
    assert cert[0].startswith("-----BEGIN CERTIFICATE-----")
    assert cert[1].startswith("-----BEGIN PRIVATE KEY-----")


def test_mtls_headers_returns_empty_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify mTLS profile returns empty headers (auth is via certificates)."""
    monkeypatch.setenv("CERT_CONTENT", "cert")
    monkeypatch.setenv("KEY_CONTENT", "key")

    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "mtls": {
                    "type": "mtls",
                    "certificate": {"from_env": "CERT_CONTENT"},
                    "private_key": {"from_env": "KEY_CONTENT"},
                }
            }
        }
    )

    auth = ProfileAuthentication(security, "mtls")
    headers = auth.headers("https://api.example.com")
    assert headers == {}


def test_mtls_with_ca_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify mTLS profile with CA bundle returns it."""
    monkeypatch.setenv("CERT_CONTENT", "cert")
    monkeypatch.setenv("KEY_CONTENT", "key")
    monkeypatch.setenv("CA_BUNDLE", "/path/to/ca-bundle.crt")

    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "mtls": {
                    "type": "mtls",
                    "certificate": {"from_env": "CERT_CONTENT"},
                    "private_key": {"from_env": "KEY_CONTENT"},
                    "ca_bundle": {"from_env": "CA_BUNDLE"},
                }
            }
        }
    )

    auth = ProfileAuthentication(security, "mtls")
    ca = auth.ca_bundle()

    assert ca == "/path/to/ca-bundle.crt"


@pytest.mark.asyncio
async def test_oauth2_missing_credentials_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLIENT_ID", raising=False)
    monkeypatch.delenv("CLIENT_SECRET", raising=False)
    calls = []
    auth = ProfileAuthentication(
        _oauth_security(),
        "oauth",
        token_http=HttpClient(
            transport=httpx.MockTransport(
                lambda request: calls.append(request) or httpx.Response(500)
            )
        ),
    )
    with pytest.raises(ToolError) as error:
        await auth.prepare("https://api.example.com", remaining_timeout=1)
    assert error.value.details["reason"] == "credential_unavailable" and calls == []


def test_mtls_missing_credentials_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify mTLS fails closed when credentials are unavailable."""
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "mtls": {
                    "type": "mtls",
                    "certificate": {"from_env": "MISSING_CERT"},
                    "private_key": {"from_env": "MISSING_KEY"},
                }
            }
        }
    )

    auth = ProfileAuthentication(security, "mtls")

    with pytest.raises(ValueError, match="required deployment secret is unavailable"):
        auth.client_cert()
