"""Resolution of OpenWorkflow HTTP authentication policies."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

import httpx

from openworkflow_adk.security.security import guarded_async_client, resolve_secret, validate_egress


def _secret(value: Any, environ: Mapping[str, str]) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("use"), str):
        name = value["use"]
        return resolve_secret(name, dict(environ))
    return None


def _secret_mapping(value: Any, environ: Mapping[str, str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping) or not isinstance(value.get("use"), str):
        return None
    resolved = resolve_secret(value["use"], dict(environ))
    if not resolved:
        return None
    try:
        result = json.loads(resolved)
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, Mapping) else None


class OAuth2ClientCredentialsAuth(httpx.Auth):
    """Fetch a bearer token for each request using client credentials."""

    requires_request_body = True

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str | None,
        scopes: list[str] | None = None,
        client_authentication: str = "client_secret_post",
    ) -> None:
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes or []
        self.client_authentication = client_authentication

    async def async_auth_flow(self, request: httpx.Request):
        form: dict[str, str] = {"grant_type": "client_credentials", "client_id": self.client_id}
        if self.scopes:
            form["scope"] = " ".join(self.scopes)
        auth = None
        if self.client_authentication == "client_secret_basic":
            auth = httpx.BasicAuth(self.client_id, self.client_secret or "")
        else:
            form["client_secret"] = self.client_secret or ""
        validate_egress(self.token_url)
        async with guarded_async_client() as client:
            response = await client.post(self.token_url, data=form, auth=auth)
            response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise ValueError("OAuth2 token response did not include access_token")
        request.headers["authorization"] = f"Bearer {token}"
        yield request


def resolve_authentication(
    authentication: Mapping[str, Any] | str | None,
    policies: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[httpx.Auth | None, dict[str, str]]:
    """Resolve inline or named basic/bearer policies into HTTP settings."""
    env = os.environ if environ is None else environ
    policy: Any = authentication
    if isinstance(policy, str) and policies:
        policy = policies.get(policy)
    if isinstance(policy, Mapping) and isinstance(policy.get("use"), str) and policies:
        policy = policies.get(policy["use"])
    if not isinstance(policy, Mapping):
        return None, {}
    if isinstance(policy.get("basic"), Mapping):
        basic = policy["basic"]
        secret = _secret_mapping(basic, env)
        if secret:
            basic = secret
        username = _secret(basic.get("username"), env)
        password = _secret(basic.get("password"), env)
        if username is None or password is None:
            raise ValueError("basic authentication requires username and password")
        return httpx.BasicAuth(username, password), {}
    if isinstance(policy.get("bearer"), Mapping):
        token = _secret(policy["bearer"].get("token"), env)
        if token is None:
            secret = _secret_mapping(policy["bearer"], env)
            token = secret.get("token") if secret else None
        if token is None:
            raise ValueError("bearer authentication requires a token")
        return None, {"authorization": f"Bearer {token}"}
    if isinstance(policy.get("digest"), Mapping):
        digest = policy["digest"]
        secret = _secret_mapping(digest, env)
        if secret:
            digest = secret
        username = _secret(digest.get("username"), env)
        password = _secret(digest.get("password"), env)
        if username is None or password is None:
            raise ValueError("digest authentication requires username and password")
        return httpx.DigestAuth(username, password), {}
    for key in ("oauth2", "oidc"):
        if isinstance(policy.get(key), Mapping):
            oauth = policy[key]
            secret = _secret_mapping(oauth, env)
            if secret:
                oauth = secret
            client = oauth.get("client") or {}
            token_url = oauth.get("authority")
            grant = oauth.get("grant", "client_credentials")
            if grant != "client_credentials" or not token_url or not client.get("id"):
                raise NotImplementedError("only OAuth2 client_credentials is supported")
            return (
                OAuth2ClientCredentialsAuth(
                    token_url,
                    str(client["id"]),
                    _secret(client.get("secret"), env),
                    list(oauth.get("scopes") or []),
                    str(client.get("authentication", "client_secret_post")),
                ),
                {},
            )
    return None, {}
