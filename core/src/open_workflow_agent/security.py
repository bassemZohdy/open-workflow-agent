"""Framework-neutral security profiles and authorization vocabulary.

Credentials remain deployment-owned references. This module deliberately does
not implement enterprise federation, delegated-user token exchange, or consent;
protocol adapters consume the same named profiles without placing raw secrets
inside workflow documents or persisted invocation state.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class _StrictSecurityModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        hide_input_in_errors=True,
    )


class SecretReference(_StrictSecurityModel):
    """Reference a sensitive deployment value without storing the value itself."""

    from_env: str

    @field_validator("from_env")
    @classmethod
    def validate_environment_name(cls, value: str) -> str:
        selected = value.strip()
        if not _ENVIRONMENT_NAME.fullmatch(selected):
            raise ValueError("secret from_env must be a valid environment variable name")
        return selected


class _PrincipalProfile(_StrictSecurityModel):
    principal: str | None = None
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    audience: list[str] = Field(default_factory=list)

    @field_validator("roles", "scopes", "audience")
    @classmethod
    def validate_unique_values(cls, value: list[str]) -> list[str]:
        selected = [item.strip() for item in value]
        if any(not item for item in selected):
            raise ValueError("security principal attributes cannot contain empty values")
        if len(selected) != len(set(selected)):
            raise ValueError("security principal attributes cannot contain duplicates")
        return selected


class BearerSecurityProfile(_PrincipalProfile):
    type: Literal["bearer"] = "bearer"
    token: SecretReference


class ApiKeySecurityProfile(_PrincipalProfile):
    type: Literal["api_key"] = "api_key"
    key: SecretReference
    header: str = "X-API-Key"

    @field_validator("header")
    @classmethod
    def validate_header_name(cls, value: str) -> str:
        selected = value.strip()
        if not _HEADER_NAME.fullmatch(selected):
            raise ValueError("api_key header must be a valid HTTP header name")
        return selected


class OAuth2ClientCredentialsSecurityProfile(_StrictSecurityModel):
    type: Literal["oauth2_client_credentials"] = "oauth2_client_credentials"
    token_url: str
    client_id: SecretReference
    client_secret: SecretReference
    scopes: list[str] = Field(default_factory=list)
    audience: str | None = None

    @field_validator("token_url")
    @classmethod
    def validate_token_url(cls, value: str) -> str:
        selected = value.strip()
        parsed = urlparse(selected)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("oauth2_client_credentials token_url must be an absolute HTTPS URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("oauth2_client_credentials token_url cannot contain credentials")
        return selected

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        selected = [scope.strip() for scope in value]
        if any(not scope for scope in selected) or len(selected) != len(set(selected)):
            raise ValueError("oauth2_client_credentials scopes must be unique non-empty values")
        return selected


class MtlsSecurityProfile(_StrictSecurityModel):
    type: Literal["mtls"] = "mtls"
    certificate: SecretReference
    private_key: SecretReference
    ca_bundle: SecretReference | None = None


SecurityProfile = Annotated[
    BearerSecurityProfile
    | ApiKeySecurityProfile
    | OAuth2ClientCredentialsSecurityProfile
    | MtlsSecurityProfile,
    Field(discriminator="type"),
]


class SecurityConfig(_StrictSecurityModel):
    profiles: dict[str, SecurityProfile] = Field(default_factory=dict)

    @field_validator("profiles")
    @classmethod
    def validate_profile_names(
        cls, value: dict[str, SecurityProfile]
    ) -> dict[str, SecurityProfile]:
        normalized: dict[str, SecurityProfile] = {}
        for name, profile in value.items():
            selected = name.strip()
            if not selected or selected != name:
                raise ValueError("security profile names must be non-empty and already trimmed")
            if selected in normalized:
                raise ValueError("security profile names must be unique")
            normalized[selected] = profile
        return normalized

    def profile(self, name: str) -> SecurityProfile:
        try:
            return self.profiles[name]
        except KeyError as exc:
            raise ValueError(f"unknown security profile: {name}") from exc


@dataclass(frozen=True, slots=True)
class Principal:
    identity: str
    roles: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    audience: frozenset[str] = frozenset()


class AuthorizationRule(_StrictSecurityModel):
    actions: list[str]
    resources: list[str] = Field(default_factory=lambda: ["*"])
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    audience: list[str] = Field(default_factory=list)

    @field_validator("actions", "resources", "roles", "scopes", "audience")
    @classmethod
    def validate_rule_values(cls, value: list[str]) -> list[str]:
        selected = [item.strip() for item in value]
        if any(not item for item in selected):
            raise ValueError("authorization rule values cannot be empty")
        if len(selected) != len(set(selected)):
            raise ValueError("authorization rule values cannot contain duplicates")
        return selected

    @model_validator(mode="after")
    def validate_required_sets(self) -> AuthorizationRule:
        if not self.actions:
            raise ValueError("authorization rule requires at least one action")
        if not self.resources:
            raise ValueError("authorization rule requires at least one resource")
        return self


class AuthorizationPolicy(_StrictSecurityModel):
    rules: list[AuthorizationRule] = Field(default_factory=list)


def resolve_secret(reference: SecretReference) -> str:
    """Resolve one secret at the last responsible moment without caching it."""

    value = os.getenv(reference.from_env)
    if value is None or not value:
        raise ValueError(f"required deployment secret is unavailable: {reference.from_env}")
    return value


def static_principal(profile: BearerSecurityProfile | ApiKeySecurityProfile) -> Principal:
    """Build the deployment-defined principal attached to a static credential."""

    return Principal(
        identity=profile.principal or "deployment-client",
        roles=frozenset(profile.roles),
        scopes=frozenset(profile.scopes),
        audience=frozenset(profile.audience),
    )


def authorize(
    principal: Principal,
    policy: AuthorizationPolicy,
    *,
    action: str,
    resource: str,
) -> bool:
    """Evaluate explicit action/resource policy while keeping roles/scopes distinct."""

    for rule in policy.rules:
        if action not in rule.actions and "*" not in rule.actions:
            continue
        if resource not in rule.resources and "*" not in rule.resources:
            continue
        if rule.roles and not set(rule.roles).issubset(principal.roles):
            continue
        if rule.scopes and not set(rule.scopes).issubset(principal.scopes):
            continue
        if rule.audience and not set(rule.audience).intersection(principal.audience):
            continue
        return True
    return False


__all__ = [
    "ApiKeySecurityProfile",
    "AuthorizationPolicy",
    "AuthorizationRule",
    "BearerSecurityProfile",
    "MtlsSecurityProfile",
    "OAuth2ClientCredentialsSecurityProfile",
    "Principal",
    "SecretReference",
    "SecurityConfig",
    "SecurityProfile",
    "authorize",
    "resolve_secret",
    "static_principal",
]
