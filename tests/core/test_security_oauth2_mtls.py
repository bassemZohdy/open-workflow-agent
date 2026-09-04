"""Tests for OAuth2 client-credentials and mTLS security profile support."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from open_workflow_agent.security import (
    MtlsSecurityProfile,
    OAuth2ClientCredentialsSecurityProfile,
    ProfileAuthentication,
    SecretReference,
    SecurityConfig,
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


def test_oauth2_headers_returns_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify OAuth2 profile returns Bearer token in headers."""
    monkeypatch.setenv("CLIENT_ID", "test-client")
    monkeypatch.setenv("CLIENT_SECRET", "test-secret")

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

    # Mock the urllib.request.urlopen to return a token response
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(
        {"access_token": "test-token-123", "expires_in": 3600}
    ).encode()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        headers = auth.headers("https://api.example.com")
        assert headers == {"Authorization": "Bearer test-token-123"}


def test_oauth2_token_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify OAuth2 tokens are cached until expiry."""
    monkeypatch.setenv("CLIENT_ID", "test-client")
    monkeypatch.setenv("CLIENT_SECRET", "test-secret")

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

    call_count = 0

    def mock_urlopen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"access_token": f"token-{call_count}", "expires_in": 3600}
        ).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        return mock_response

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        # First call should fetch token
        headers1 = auth.headers("https://api.example.com")
        assert headers1["Authorization"] == "Bearer token-1"
        assert call_count == 1

        # Second call should use cached token
        headers2 = auth.headers("https://api.example.com")
        assert headers2["Authorization"] == "Bearer token-1"
        assert call_count == 1  # No additional call


def test_mtls_client_cert_returns_certificate_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify mTLS profile returns certificate and key."""
    monkeypatch.setenv("CERT_CONTENT", "-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----")
    monkeypatch.setenv("KEY_CONTENT", "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----")

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


def test_oauth2_missing_credentials_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify OAuth2 fails closed when credentials are unavailable."""
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "oauth": {
                    "type": "oauth2_client_credentials",
                    "token_url": "https://auth.example.com/token",
                    "client_id": {"from_env": "MISSING_CLIENT_ID"},
                    "client_secret": {"from_env": "MISSING_CLIENT_SECRET"},
                }
            }
        }
    )

    auth = ProfileAuthentication(security, "oauth")

    with pytest.raises(ValueError, match="required deployment secret is unavailable"):
        auth.headers("https://api.example.com")


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
