import socket
from unittest.mock import patch

import pytest

from openworkflow_adk.security.security import (
    EgressDeniedError,
    guarded_async_client,
    guarded_client,
    resolve_secret,
    validate_egress,
)


def test_egress_guard_blocks_private_targets_by_default() -> None:
    with pytest.raises(EgressDeniedError):
        validate_egress("http://127.0.0.1:8080/admin")


def test_egress_guard_blocks_metadata_service_address() -> None:
    with pytest.raises(EgressDeniedError):
        validate_egress("http://169.254.169.254/latest/meta-data")


def test_egress_guard_allows_explicit_host_allowlist() -> None:
    validate_egress(
        "http://127.0.0.1:8080/admin",
        {"WORKFLOW_EGRESS_ALLOWLIST": "127.0.0.1"},
    )


def test_airgapped_mode_blocks_all_network_egress() -> None:
    with pytest.raises(EgressDeniedError, match="air-gapped"):
        validate_egress("https://example.com", {"WORKFLOW_AIRGAPPED": "1"})


def test_resolve_secret_reads_prefixed_variable() -> None:
    assert resolve_secret("api-key", {"WORKFLOW_SECRET__api-key": "secret"}) == "secret"


def test_resolve_secret_ignores_raw_name_by_default() -> None:
    assert resolve_secret("PATH", {"PATH": "/bin"}) is None


def test_resolve_secret_allows_raw_name_with_opt_in() -> None:
    assert (
        resolve_secret(
            "PATH",
            {"PATH": "/bin", "WORKFLOW_SECRETS_ALLOW_RAW": "1"},
        )
        == "/bin"
    )


def test_egress_guard_resolves_dns_by_default() -> None:
    # Hostnames are resolved and blocked by default (fail-closed).
    with patch(
        "openworkflow_adk.security.security.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("169.254.169.254", 80))],
    ):
        with pytest.raises(EgressDeniedError, match="blocked"):
            validate_egress("https://metadata.example", {})


def test_egress_guard_blocks_unresolvable_hosts_by_default() -> None:
    with patch(
        "openworkflow_adk.security.security.socket.getaddrinfo",
        side_effect=socket.gaierror("Name or service not known"),
    ):
        with pytest.raises(EgressDeniedError, match="could not resolve"):
            validate_egress("https://definitely-not-a-real-host.invalid", {})


def test_egress_guard_skips_dns_resolution_when_configured(monkeypatch) -> None:
    # Legacy pass-through remains available for test doubles and offline mocks.
    monkeypatch.setenv("WORKFLOW_EGRESS_SKIP_DNS", "1")
    validate_egress("https://definitely-not-a-real-host.invalid")


def test_egress_guard_allows_unresolved_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_EGRESS_ALLOW_UNRESOLVED", "1")
    validate_egress("https://definitely-not-a-real-host.invalid")


def test_egress_guard_blocks_hostname_resolving_to_loopback() -> None:
    with patch(
        "openworkflow_adk.security.security.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("127.0.0.1", 80))],
    ):
        with pytest.raises(EgressDeniedError, match="blocked"):
            validate_egress("https://safe.example", {})


def test_egress_guard_blocks_hostname_resolving_to_link_local() -> None:
    with patch(
        "openworkflow_adk.security.security.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("169.254.169.254", 80))],
    ):
        with pytest.raises(EgressDeniedError, match="blocked"):
            validate_egress("https://safe.example", {})


def test_guarded_clients_install_request_hooks() -> None:
    sync_client = guarded_client()
    async_client = guarded_async_client()
    try:
        assert sync_client._event_hooks["request"]
        assert async_client._event_hooks["request"]
    finally:
        sync_client.close()
        import asyncio

        asyncio.run(async_client.aclose())


def test_guarded_client_hook_rejects_blocked_target(monkeypatch) -> None:
    """The request hook itself rejects blocked hops when it fires."""
    from openworkflow_adk.security.security import _egress_request_hook

    with patch(
        "openworkflow_adk.security.security.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("10.0.0.1", 443))],
    ):
        hook = _egress_request_hook({})
        with pytest.raises(EgressDeniedError):
            hook(type("Request", (), {"url": "https://grpc.internal:443"})())  # type: ignore[attr-defined]


def test_grpc_host_egress_is_checked() -> None:
    """gRPC hosts must pass the same egress guard as HTTP endpoints."""
    with patch(
        "openworkflow_adk.security.security.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("10.0.0.1", 443))],
    ):
        with pytest.raises(EgressDeniedError):
            validate_egress("https://grpc.internal:443", {})
