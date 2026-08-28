from __future__ import annotations

import pytest
from open_workflow_agent.security import (
    ApiKeySecurityProfile,
    AuthorizationPolicy,
    AuthorizationRule,
    BearerSecurityProfile,
    OAuth2ClientCredentialsSecurityProfile,
    Principal,
    SecretReference,
    SecurityConfig,
    authorize,
    resolve_secret,
    static_principal,
)


def test_named_profiles_are_strict_and_discriminated() -> None:
    config = SecurityConfig.model_validate(
        {
            "profiles": {
                "agent-client": {
                    "type": "bearer",
                    "token": {"from_env": "OWA_A2A_TOKEN"},
                    "principal": "agent-client",
                    "roles": ["agent"],
                    "scopes": ["message.send"],
                    "audience": ["renewal-agent"],
                },
                "partner": {
                    "type": "api_key",
                    "key": {"from_env": "PARTNER_API_KEY"},
                    "header": "X-Partner-Key",
                },
                "service-oauth": {
                    "type": "oauth2_client_credentials",
                    "token_url": "https://identity.example.com/oauth2/token",
                    "client_id": {"from_env": "SERVICE_CLIENT_ID"},
                    "client_secret": {"from_env": "SERVICE_CLIENT_SECRET"},
                    "scopes": ["tools.read"],
                },
                "service-mtls": {
                    "type": "mtls",
                    "certificate": {"from_env": "MTLS_CERT_FILE"},
                    "private_key": {"from_env": "MTLS_KEY_FILE"},
                    "ca_bundle": {"from_env": "MTLS_CA_FILE"},
                },
            }
        }
    )

    assert isinstance(config.profile("agent-client"), BearerSecurityProfile)
    assert isinstance(config.profile("partner"), ApiKeySecurityProfile)
    assert isinstance(config.profile("service-oauth"), OAuth2ClientCredentialsSecurityProfile)
    assert config.profile("service-mtls").type == "mtls"


def test_secret_reference_resolves_from_environment_without_storing_value(monkeypatch) -> None:
    reference = SecretReference(from_env="OWA_TEST_SECRET")
    monkeypatch.setenv("OWA_TEST_SECRET", "super-secret")

    assert resolve_secret(reference) == "super-secret"
    assert "super-secret" not in reference.model_dump_json()


def test_missing_secret_fails_closed_without_echoing_secret_value(monkeypatch) -> None:
    monkeypatch.delenv("OWA_MISSING_SECRET", raising=False)
    reference = SecretReference(from_env="OWA_MISSING_SECRET")

    with pytest.raises(ValueError, match="OWA_MISSING_SECRET") as raised:
        resolve_secret(reference)
    assert "secret-value" not in str(raised.value)


def test_static_profile_principal_keeps_roles_scopes_and_audience_distinct() -> None:
    profile = BearerSecurityProfile(
        token=SecretReference(from_env="OWA_TOKEN"),
        principal="caller-a",
        roles=["operator"],
        scopes=["tasks.get", "tasks.cancel"],
        audience=["agent-a"],
    )

    principal = static_principal(profile)
    assert principal == Principal(
        identity="caller-a",
        roles=frozenset({"operator"}),
        scopes=frozenset({"tasks.get", "tasks.cancel"}),
        audience=frozenset({"agent-a"}),
    )


def test_authorization_requires_action_resource_and_declared_constraints() -> None:
    policy = AuthorizationPolicy(
        rules=[
            AuthorizationRule(
                actions=["tasks.get"],
                resources=["skill:renewal"],
                roles=["agent"],
                scopes=["tasks.read"],
                audience=["renewal-agent"],
            )
        ]
    )
    allowed = Principal(
        identity="caller",
        roles=frozenset({"agent"}),
        scopes=frozenset({"tasks.read"}),
        audience=frozenset({"renewal-agent"}),
    )

    assert authorize(allowed, policy, action="tasks.get", resource="skill:renewal") is True
    assert authorize(allowed, policy, action="tasks.cancel", resource="skill:renewal") is False
    assert authorize(allowed, policy, action="tasks.get", resource="skill:other") is False
    assert (
        authorize(
            Principal(identity="caller", roles=frozenset({"agent"})),
            policy,
            action="tasks.get",
            resource="skill:renewal",
        )
        is False
    )


def test_security_config_rejects_inline_values_and_insecure_oauth_endpoints() -> None:
    with pytest.raises(Exception, match="from_env"):
        SecurityConfig.model_validate(
            {"profiles": {"bad": {"type": "bearer", "token": "inline-secret"}}}
        )
    with pytest.raises(Exception, match="HTTPS"):
        OAuth2ClientCredentialsSecurityProfile.model_validate(
            {
                "token_url": "http://identity.example.com/token",
                "client_id": {"from_env": "CLIENT_ID"},
                "client_secret": {"from_env": "CLIENT_SECRET"},
            }
        )


def test_authorization_rule_requires_actions() -> None:
    with pytest.raises(Exception, match="at least one action"):
        AuthorizationRule(actions=[])
