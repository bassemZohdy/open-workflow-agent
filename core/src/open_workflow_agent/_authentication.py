"""Finite deployment-pinned client credentials; no discovery or delegated grants."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlencode, urlparse

import httpx

from .errors import ToolError
from .security import OAuth2ClientCredentialsSecurityProfile, resolve_secret

if TYPE_CHECKING:
    from .protocols import HttpClient

MAX_TOKEN_BYTES = 8192
MAX_TOKEN_LIFETIME = 86_400.0
MAX_CREDENTIAL_BYTES = 16_384
MAX_TOKEN_FORM_BYTES = 131_072
MAX_PREPARATIONS = 16


def authentication_failure(reason: str) -> ToolError:
    return ToolError(
        "protocol authentication failed", details={"stage": "authentication", "reason": reason}
    )


def validate_header_value(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_TOKEN_BYTES
        or any(ord(character) < 32 or ord(character) > 126 for character in value)
    ):
        raise authentication_failure("invalid_credential")


def _token_response(
    raw: bytes, profile: OAuth2ClientCredentialsSecurityProfile, resource: str
) -> tuple[str, float]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate token field")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> Any:
        raise ValueError("nonfinite token response")

    value = json.loads(
        raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid_constant
    )
    if not isinstance(value, dict):
        raise ValueError("token response must be an object")
    token = value.get("access_token")
    token_type = value.get("token_type")
    expires = value.get("expires_in")
    lifetime = float(cast(float, expires)) if type(expires) in {int, float} else math.nan
    if (
        not isinstance(token, str)
        or not re.fullmatch(r"[A-Za-z0-9\-._~+/]+=*", token)
        or len(token) > MAX_TOKEN_BYTES
        or not isinstance(token_type, str)
        or token_type.lower() != "bearer"
        or type(expires) not in {int, float}
        or not math.isfinite(lifetime)
        or not 0 < lifetime <= MAX_TOKEN_LIFETIME
    ):
        raise ValueError("invalid finite bearer token")
    scope = value.get("scope")
    if scope is not None:
        if not isinstance(scope, str) or set(scope.split(" ")) != set(profile.scopes):
            raise ValueError("token scope differs from declared request")
    if "resource" in value and value["resource"] != resource:
        raise ValueError("token resource differs from declared request")
    if "issuer" in value and value["issuer"] != profile.issuer:
        raise ValueError("token issuer differs from declared endpoint")
    return token, lifetime


class OAuthTokenProvider:
    """One bounded cache per selected named profile; tokens remain process-local."""

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http
        self._lock = asyncio.Lock()
        self._preparations = 0
        self._token: str | None = None
        self._expires_at = 0.0
        # In-memory equality detects rotation; no credential hash or value is
        # serialized, exposed by diagnostics or included in exception messages.
        self._binding: tuple[Any, ...] | None = None

    async def token(
        self,
        profile: OAuth2ClientCredentialsSecurityProfile,
        endpoint: str,
        *,
        remaining_timeout: float | None = None,
    ) -> str:
        if endpoint not in profile.allowed_resource_endpoints:
            raise authentication_failure("resource_not_allowed")
        timeout = profile.token_timeout_seconds
        if remaining_timeout is not None:
            if type(remaining_timeout) not in {int, float} or not math.isfinite(remaining_timeout):
                raise authentication_failure("invalid_deadline")
            timeout = min(timeout, remaining_timeout)
        if timeout <= 0:
            raise authentication_failure("deadline_expired")
        if self._preparations >= MAX_PREPARATIONS:
            raise authentication_failure("preparation_capacity")
        self._preparations += 1
        deadline = time.monotonic() + timeout
        try:
            async with asyncio.timeout(timeout), self._lock:
                client_id, client_secret = (
                    resolve_secret(profile.client_id),
                    resolve_secret(profile.client_secret),
                )
                ca_bundle = resolve_secret(profile.ca_bundle) if profile.ca_bundle else None
                if any(
                    len(value.encode()) > MAX_CREDENTIAL_BYTES
                    for value in (client_id, client_secret)
                ):
                    raise authentication_failure("invalid_credential")
                binding = (
                    profile.model_dump_json(),
                    endpoint,
                    client_id,
                    client_secret,
                    ca_bundle,
                    self._transport_binding(),
                )
                if self._binding == binding and self._token and time.monotonic() < self._expires_at:
                    return self._token
                # A changed endpoint, policy, credential or transport cannot
                # inherit the previous profile's token, even on fetch failure.
                self._token, self._binding, self._expires_at = None, None, 0.0
                requested_at = time.monotonic()
                token, lifetime = await self._fetch(
                    profile,
                    client_id,
                    client_secret,
                    deadline,
                    ca_bundle,
                    profile.resource or endpoint,
                )
                now = time.monotonic()
                if now >= deadline:
                    raise authentication_failure("deadline_expired")
                remaining_lifetime = lifetime - (now - requested_at)
                if remaining_lifetime <= 0:
                    raise authentication_failure("token_expired")
                self._token, self._binding = token, binding
                self._expires_at = now + remaining_lifetime - min(60.0, remaining_lifetime * 0.1)
                return token
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise authentication_failure("deadline_expired") from None
        except ToolError:
            raise
        except Exception:
            raise authentication_failure("credential_unavailable") from None
        finally:
            self._preparations -= 1

    def _transport_binding(self) -> tuple[Any, ...]:
        if self.http is None:
            return ()
        return (
            id(self.http.transport),
            id(self.http.destination_resolver),
            self.http.verify_tls,
            self.http.timeout,
            self.http.max_response_bytes,
            self.http.follow_redirects,
            tuple(sorted(self.http.allowed_hosts)) if self.http.allowed_hosts is not None else None,
        )

    async def _fetch(
        self,
        profile: OAuth2ClientCredentialsSecurityProfile,
        client_id: str,
        client_secret: str,
        deadline: float,
        ca_bundle: str | None,
        resource: str,
    ) -> tuple[str, float]:
        # Lazy import avoids a security/protocol composition cycle. All token
        # traffic uses the same bounded common transport as business requests.
        from .protocols import HttpClient, _normalize_hostname

        source = self.http
        host = urlparse(profile.token_url).hostname
        if source is not None and (
            source.verify_tls is False
            or source.follow_redirects
            or (
                source.allowed_hosts is not None
                and (
                    host is None
                    or _normalize_hostname(host)
                    not in {_normalize_hostname(value) for value in source.allowed_hosts}
                )
            )
        ):
            raise authentication_failure("issuer_transport_not_allowed")
        timeout = min(
            profile.token_timeout_seconds,
            source.timeout if source else profile.token_timeout_seconds,
        )
        cap = min(
            profile.max_token_response_bytes,
            source.max_response_bytes if source else profile.max_token_response_bytes,
        )
        if type(cap) is not int or cap <= 0 or not math.isfinite(timeout) or timeout <= 0:
            raise authentication_failure("issuer_transport_not_allowed")
        http = HttpClient(
            timeout=timeout,
            max_response_bytes=cap,
            verify_tls=True,
            ca_bundle=ca_bundle
            or (source.verify_tls if source and isinstance(source.verify_tls, str) else None),
            follow_redirects=False,
            allowed_hosts={host} if host else set(),
            transport=source.transport if source else None,
            destination_resolver=source.destination_resolver if source else None,
        )
        form = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            **({"scope": " ".join(profile.scopes)} if profile.scopes else {}),
            **({"audience": profile.audience} if profile.audience else {}),
            "resource": resource,
        }
        encoded_form = urlencode(form).encode()
        if len(encoded_form) > MAX_TOKEN_FORM_BYTES:
            raise authentication_failure("invalid_credential")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise authentication_failure("deadline_expired")
        try:
            response = await http.request(
                "POST",
                profile.token_url,
                content=encoded_form,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                request_timeout=remaining,
                _return_response=True,
            )
            if not isinstance(response, httpx.Response) or response.status_code != 200:
                raise authentication_failure("token_response_rejected")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json" or len(response.content) > cap:
                raise authentication_failure("token_response_rejected")
            return _token_response(response.content, profile, resource)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise authentication_failure("token_response_rejected") from None
