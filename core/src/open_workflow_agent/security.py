"""Framework-neutral security profiles and authorization vocabulary.

Credentials remain deployment-owned references. This module deliberately does
not implement enterprise federation, delegated-user token exchange, or consent;
protocol adapters consume the same named profiles without placing raw secrets
inside workflow documents or persisted invocation state.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from ._authentication import OAuthTokenProvider
    from .protocols import HttpClient

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
    issuer: str | None = None
    resource: str | None = None
    allowed_resource_endpoints: list[str] = Field(default_factory=list)
    token_timeout_seconds: float = Field(default=10.0, gt=0, le=120, allow_inf_nan=False)
    max_token_response_bytes: int = Field(default=65_536, gt=0, le=1_048_576)
    ca_bundle: SecretReference | None = None

    @field_validator("token_url")
    @classmethod
    def validate_token_url(cls, value: str) -> str:
        selected = value.strip()
        parsed = urlparse(selected)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or len(selected) > 4096
            or any(ord(character) < 33 for character in selected)
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError("oauth2_client_credentials token_url must be an absolute HTTPS URL")
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("oauth2_client_credentials token_url cannot contain credentials")
        return selected

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        selected = [scope.strip() for scope in value]
        if (
            len(selected) > 32
            or any(
                not scope
                or not re.fullmatch(r"[\x21\x23-\x5b\x5d-\x7e]+", scope)
                or len(scope) > 128
                for scope in selected
            )
            or len(selected) != len(set(selected))
        ):
            raise ValueError("oauth2_client_credentials scopes must be unique non-empty values")
        return selected

    @field_validator("issuer", "resource")
    @classmethod
    def validate_trusted_uri(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or len(value) > 4096
            or any(ord(character) < 33 for character in value)
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError("OAuth issuer/resource must be an exact absolute HTTPS URI")
        return value

    @field_validator("allowed_resource_endpoints")
    @classmethod
    def validate_resource_endpoints(cls, values: list[str]) -> list[str]:
        selected = [cls.validate_trusted_uri(value) for value in values]
        if (
            len(values) > 32
            or any(value is None for value in selected)
            or len(values) != len(set(values))
        ):
            raise ValueError("OAuth resource endpoints must be exact unique HTTPS URIs")
        return values

    @field_validator("audience")
    @classmethod
    def validate_audience(cls, value: str | None) -> str | None:
        if value is not None and (
            not value or len(value) > 2048 or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("OAuth audience must be a bounded literal value")
        return value

    @model_validator(mode="after")
    def validate_issuer_binding(self) -> OAuth2ClientCredentialsSecurityProfile:
        if self.issuer:
            issuer, token = urlparse(self.issuer), urlparse(self.token_url)
            if (issuer.hostname, issuer.port or 443) != (token.hostname, token.port or 443):
                raise ValueError("OAuth token endpoint must belong to the declared issuer origin")
        return self


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


@dataclass(frozen=True, slots=True, repr=False)
class AuthenticationBinding:
    """Call-local credentials; never serialized into an ordinary runtime record."""

    headers: Mapping[str, str]
    client_cert: tuple[str, str] | None = None
    ca_bundle: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))

    def __repr__(self) -> str:
        return "AuthenticationBinding(<credentials redacted>)"


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


class ProfileAuthentication:
    """Resolve a named security profile into protocol headers at call time.

    Existence and header capability are validated at construction so unknown
    profile references fail closed at startup. The credential value itself is
    resolved from its deployment environment variable at the last responsible
    moment and never cached.
    """

    def __init__(
        self,
        security: SecurityConfig,
        profile_name: str,
        *,
        token_http: HttpClient | None = None,
    ) -> None:
        profile = security.profile(profile_name)
        self._supports_headers = isinstance(profile, (BearerSecurityProfile, ApiKeySecurityProfile))
        self._supports_oauth2 = isinstance(profile, OAuth2ClientCredentialsSecurityProfile)
        self._supports_mtls = isinstance(profile, MtlsSecurityProfile)
        if not (self._supports_headers or self._supports_oauth2 or self._supports_mtls):
            raise ValueError(f"security profile '{profile_name}' does not provide protocol headers")
        self.security = security
        self.profile_name = profile_name
        self._token_http = token_http
        self._oauth2_provider: OAuthTokenProvider | None = None

    def bind_transport(self, http: HttpClient) -> None:
        """Bind the composition client's policy once, before credential preparation."""
        if self._token_http is http:
            return
        if self._token_http is not None or self._oauth2_provider is not None:
            raise ValueError("authentication transport is already bound or prepared")
        self._token_http = http

    def headers(self, endpoint: str) -> Mapping[str, str]:
        del endpoint
        profile = self.security.profile(self.profile_name)
        if isinstance(profile, BearerSecurityProfile):
            return {"Authorization": f"Bearer {resolve_secret(profile.token)}"}
        if isinstance(profile, ApiKeySecurityProfile):
            return {profile.header: resolve_secret(profile.key)}
        if isinstance(profile, OAuth2ClientCredentialsSecurityProfile):
            raise ValueError("OAuth authentication requires bounded asynchronous preparation")
        if isinstance(profile, MtlsSecurityProfile):
            return {}
        raise ValueError(
            f"security profile '{self.profile_name}' does not provide protocol headers"
        )

    async def prepare(
        self, endpoint: str, *, remaining_timeout: float | None = None
    ) -> AuthenticationBinding:
        """Prepare headers/TLS under the caller's original remaining deadline."""
        from ._authentication import (
            OAuthTokenProvider,
            authentication_failure,
            validate_header_value,
        )

        try:
            profile = self.security.profile(self.profile_name)
            if isinstance(profile, OAuth2ClientCredentialsSecurityProfile):
                if self._oauth2_provider is None:
                    self._oauth2_provider = OAuthTokenProvider(self._token_http)
                token = await self._oauth2_provider.token(
                    profile, endpoint, remaining_timeout=remaining_timeout
                )
                return AuthenticationBinding(
                    {"Authorization": "Bearer " + token},
                    ca_bundle=resolve_secret(profile.ca_bundle) if profile.ca_bundle else None,
                )
            headers = self.headers(endpoint)
            for value in headers.values():
                validate_header_value(value)
            certificate, ca_bundle = self.client_cert(), self.ca_bundle()
            if isinstance(profile, MtlsSecurityProfile):
                if certificate is None or not all(
                    Path(path).is_file()
                    for path in (*certificate, *((ca_bundle,) if ca_bundle is not None else ()))
                ):
                    raise authentication_failure("credential_unavailable")
            return AuthenticationBinding(headers, certificate, ca_bundle)
        except ValueError:
            raise authentication_failure("credential_unavailable") from None

    def client_cert(self) -> tuple[str, str] | None:
        """Return (certificate, private_key) for mTLS profiles."""
        profile = self.security.profile(self.profile_name)
        if isinstance(profile, MtlsSecurityProfile):
            return (resolve_secret(profile.certificate), resolve_secret(profile.private_key))
        return None

    def ca_bundle(self) -> str | None:
        """Return CA bundle path/content for mTLS profiles."""
        profile = self.security.profile(self.profile_name)
        if isinstance(profile, MtlsSecurityProfile) and profile.ca_bundle:
            return resolve_secret(profile.ca_bundle)
        return None


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
    "AuthenticationBinding",
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
    "ProfileAuthentication",
    "resolve_secret",
    "static_principal",
]
