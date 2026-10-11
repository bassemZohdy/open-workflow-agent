"""Runtime security guards for external workflow resources."""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import httpx


class EgressDeniedError(PermissionError):
    """Raised when a workflow attempts a disallowed network egress."""


def resolve_secret(name: str, environ: dict[str, str] | None = None) -> str | None:
    """Resolve a named secret from deployment environment variables.

    Secrets are read only from ``WORKFLOW_SECRET__<NAME>`` prefixed variables.
    A fallback to the bare variable name is available only when
    ``WORKFLOW_SECRETS_ALLOW_RAW=1`` is set.
    """
    values = os.environ if environ is None else environ
    prefixed = values.get(f"WORKFLOW_SECRET__{name}")
    if prefixed is not None:
        return prefixed
    if values.get("WORKFLOW_SECRETS_ALLOW_RAW", "").lower() in {"1", "true", "yes"}:
        return values.get(name)
    return None


def redact(value: object, secrets: list[str] | tuple[str, ...] = ()) -> object:
    """Recursively redact known secret values from persisted/logged data."""
    secret_values = [item for item in secrets if item]
    if isinstance(value, str):
        result = value
        for secret in secret_values:
            result = result.replace(secret, "[REDACTED]")
        return result
    if isinstance(value, dict):
        return {key: redact(item, secret_values) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secret_values) for item in value]
    return value


def _block_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address, host: str) -> None:
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    ):
        raise EgressDeniedError(f"egress to {host!r} is blocked")


def validate_egress(url: str, environ: dict[str, str] | None = None) -> None:
    """Reject loopback/private/link-local targets unless explicitly allowlisted.

    The guard fails closed: every hostname is resolved and every resulting
    address is checked, so a DNS name that resolves to a blocked range (for
    example ``169.254.169.254``) is denied even when the workflow only knows the
    name. DNS failures are treated as blocked unless
    ``WORKFLOW_EGRESS_ALLOW_UNRESOLVED=1`` is set.

    Two escape hatches exist for controlled deployments:

    - ``WORKFLOW_EGRESS_ALLOWLIST`` — a comma-separated list of exact hosts that
      bypass the address check (intended for trusted internal services).
    - ``WORKFLOW_EGRESS_SKIP_DNS=1`` — restore the legacy behavior of passing
      non-IP hostnames through without resolution (needed by test doubles and
      offline mocks; never enable it on an untrusted boundary).
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise EgressDeniedError(f"unsupported egress scheme: {parsed.scheme or '<none>'}")
    host = (parsed.hostname or "").lower().rstrip(".")
    values = os.environ if environ is None else environ
    if values.get("WORKFLOW_AIRGAPPED", "").lower() in {"1", "true", "yes"}:
        raise EgressDeniedError("network egress is disabled in air-gapped mode")
    allowlist = {
        item.strip().lower().rstrip(".")
        for item in values.get("WORKFLOW_EGRESS_ALLOWLIST", "").split(",")
        if item.strip()
    }
    if host in allowlist:
        return
    if host in {"localhost", "localhost.localdomain"}:
        raise EgressDeniedError(f"egress to {host!r} is blocked")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if values.get("WORKFLOW_EGRESS_SKIP_DNS", "").lower() in {"1", "true", "yes"}:
            return
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            if values.get("WORKFLOW_EGRESS_ALLOW_UNRESOLVED", "").lower() in {"1", "true", "yes"}:
                return
            raise EgressDeniedError(f"could not resolve {host!r} for egress check") from exc
        for info in infos:
            _block_address(ipaddress.ip_address(info[4][0]), host)
        return
    _block_address(address, host)


def _egress_request_hook(environ: Mapping[str, str] | None) -> Any:
    """Return a sync httpx request hook that validates the target URL.

    The hook fires immediately before every request is dispatched, which for
    redirect-following clients means every redirect hop is re-validated against
    the (possibly rebound) DNS answer, closing the resolve-then-connect window.
    """

    def hook(request: httpx.Request) -> None:
        validate_egress(str(request.url), dict(environ) if environ is not None else None)

    return hook


def _egress_request_hook_async(environ: Mapping[str, str] | None) -> Any:
    """Return an async httpx request hook (required by ``AsyncClient``)."""

    async def hook(request: httpx.Request) -> None:
        validate_egress(str(request.url), dict(environ) if environ is not None else None)

    return hook


def guarded_client(environ: dict[str, str] | None = None, **kwargs: Any) -> httpx.Client:
    """Build a sync httpx client whose every request hop passes the egress guard."""
    hooks = dict(kwargs.pop("event_hooks", {}) or {})
    hooks.setdefault("request", []).append(_egress_request_hook(environ))
    return httpx.Client(event_hooks=hooks, **kwargs)


def guarded_async_client(environ: dict[str, str] | None = None, **kwargs: Any) -> httpx.AsyncClient:
    """Build an async httpx client whose every request hop passes the egress guard."""
    hooks = dict(kwargs.pop("event_hooks", {}) or {})
    hooks.setdefault("request", []).append(_egress_request_hook_async(environ))
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)
