"""Real issuer and per-request certificate identity stay inside selected bindings."""

from __future__ import annotations

import asyncio

import pytest
from _auth_tls_fixture import auth_tls_receiver
from open_workflow_agent.errors import ToolError
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig


@pytest.fixture(scope="module")
def tls_receiver(tmp_path_factory):
    with auth_tls_receiver(tmp_path_factory.mktemp("authentication-tls")) as server:
        yield server


def oauth(monkeypatch, server, client):
    monkeypatch.setenv("REAL_AUTH_ID", "local-client")
    monkeypatch.setenv("REAL_AUTH_SECRET", "private-local-secret")
    monkeypatch.setenv("REAL_AUTH_CA", server.ca_bundle)
    profile = {
        "type": "oauth2_client_credentials",
        "token_url": server.origin + "/token",
        "issuer": server.origin,
        "resource": server.origin + "/resource",
        "allowed_resource_endpoints": [server.origin + "/resource"],
        "client_id": {"from_env": "REAL_AUTH_ID"},
        "client_secret": {"from_env": "REAL_AUTH_SECRET"},
        "ca_bundle": {"from_env": "REAL_AUTH_CA"},
    }
    return ProfileAuthentication(
        SecurityConfig.model_validate({"profiles": {"oauth": profile}}), "oauth", token_http=client
    )


@pytest.mark.asyncio
async def test_real_tls_issuer_and_resource_do_not_inherit_unrelated_global_client_identity(
    monkeypatch, tls_receiver
):
    server = tls_receiver
    client = HttpClient(
        timeout=3,
        allowed_hosts={"127.0.0.1"},
        ca_bundle=server.ca_bundle,
        client_cert=tuple(str(path) for path in server.certificates["client-A"]),
    )
    auth = oauth(monkeypatch, server, client)
    before = len(server.requests)
    result = await client.request(
        "POST", server.origin + "/resource", json={}, request_timeout=3, _authentication=auth
    )
    assert result == {"accepted": True}
    requests = server.requests[before:]
    assert [request[0] for request in requests] == ["/token", "/resource"]
    assert all(request[3] is None for request in requests)
    assert requests[1][1]["Authorization"] == "Bearer real-issued-token"
    assert client.client_cert == tuple(str(path) for path in server.certificates["client-A"])


@pytest.mark.asyncio
@pytest.mark.parametrize("network_mode", ["direct", "pinned"])
async def test_simultaneous_selected_mtls_profiles_preserve_their_own_certificate_and_ca(
    monkeypatch, tls_receiver, network_mode
):
    server = tls_receiver
    profiles = {}
    for name in ("client-A", "client-B"):
        certificate, key = server.certificates[name]
        environment = name.replace("-", "_").upper()
        monkeypatch.setenv(environment + "_CERT", str(certificate))
        monkeypatch.setenv(environment + "_KEY", str(key))
        profiles[name] = {
            "type": "mtls",
            "certificate": {"from_env": environment + "_CERT"},
            "private_key": {"from_env": environment + "_KEY"},
            "ca_bundle": {"from_env": "REAL_MTLS_CA"},
        }
    monkeypatch.setenv("REAL_MTLS_CA", server.ca_bundle)
    security = SecurityConfig.model_validate({"profiles": profiles})
    global_certificate = tuple(str(path) for path in server.certificates["client-A"])

    async def pinned(endpoint):
        assert endpoint == server.origin + "/mtls"
        return ("127.0.0.1",)

    client = HttpClient(
        timeout=3,
        ca_bundle=server.ca_bundle,
        client_cert=global_certificate,
        destination_resolver=pinned if network_mode == "pinned" else None,
    )
    values = await asyncio.gather(
        *[
            client.request(
                "POST",
                server.origin + "/mtls",
                json={},
                request_timeout=3,
                _authentication=ProfileAuthentication(security, name),
            )
            for name in ("client-A", "client-B")
        ]
    )
    assert values == [{"identity": "client-A"}, {"identity": "client-B"}]
    assert client.client_cert == global_certificate and client.verify_tls == server.ca_bundle


@pytest.mark.asyncio
async def test_missing_selected_certificate_rejects_before_real_receiver_io(
    monkeypatch, tls_receiver
):
    server = tls_receiver
    monkeypatch.setenv("MISSING_MTLS_CERT", str(server.certificates["client-A"][0]) + ".missing")
    monkeypatch.setenv("MISSING_MTLS_KEY", str(server.certificates["client-A"][1]))
    auth = ProfileAuthentication(
        SecurityConfig.model_validate(
            {
                "profiles": {
                    "missing": {
                        "type": "mtls",
                        "certificate": {"from_env": "MISSING_MTLS_CERT"},
                        "private_key": {"from_env": "MISSING_MTLS_KEY"},
                    }
                }
            }
        ),
        "missing",
    )
    before = len(server.requests)
    with pytest.raises(ToolError) as error:
        await HttpClient(timeout=3).request(
            "POST", server.origin + "/mtls", json={}, _authentication=auth
        )
    assert error.value.details["stage"] == "authentication"
    assert len(server.requests) == before
