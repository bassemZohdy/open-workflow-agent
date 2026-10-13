"""Finite client credentials fail before resource dispatch and never leak secrets."""

from __future__ import annotations

import asyncio
import json
import ssl
from urllib.parse import parse_qs

import httpx
import pytest
from open_workflow_agent.errors import ToolError
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig

TOKEN = {"access_token": "issued-token", "token_type": "Bearer", "expires_in": 300}
RESOURCE = "https://resource.test/mcp"
ISSUER = "https://issuer.test/token"


def authentication(monkeypatch, handler, **overrides):
    monkeypatch.setenv("ASYNC_AUTH_CLIENT", "client-identity")
    monkeypatch.setenv("ASYNC_AUTH_SECRET", "private-client-secret")
    profile = {
        "type": "oauth2_client_credentials",
        "token_url": ISSUER,
        "issuer": "https://issuer.test",
        "resource": RESOURCE,
        "client_id": {"from_env": "ASYNC_AUTH_CLIENT"},
        "client_secret": {"from_env": "ASYNC_AUTH_SECRET"},
        "allowed_resource_endpoints": [RESOURCE],
        "scopes": ["tools:read"],
        **overrides,
    }
    security = SecurityConfig.model_validate({"profiles": {"selected": profile}})
    return ProfileAuthentication(
        security, "selected", token_http=HttpClient(transport=httpx.MockTransport(handler))
    )


@pytest.mark.asyncio
async def test_async_token_preparation_is_resource_bound_cached_and_rotates_with_credentials(
    monkeypatch,
):
    requests = []

    def issuer(request):
        requests.append(request)
        assert str(request.url) == ISSUER
        assert "authorization" not in request.headers
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["client_credentials"]
        assert form["resource"] == [RESOURCE] and form["scope"] == ["tools:read"]
        return httpx.Response(200, json={**TOKEN, "access_token": f"token-{len(requests)}"})

    auth = authentication(monkeypatch, issuer)
    first = await auth.prepare(RESOURCE, remaining_timeout=1)
    second = await auth.prepare(RESOURCE, remaining_timeout=1)
    assert first.headers == second.headers == {"Authorization": "Bearer token-1"}
    assert len(requests) == 1
    monkeypatch.setenv("ASYNC_AUTH_SECRET", "rotated-private-secret")
    third = await auth.prepare(RESOURCE, remaining_timeout=1)
    assert third.headers == {"Authorization": "Bearer token-2"} and len(requests) == 2
    assert "token-" not in repr(third) and "secret" not in repr(third)
    with pytest.raises(TypeError):
        third.headers["Authorization"] = "forged"


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {},
        {**TOKEN, "access_token": ""},
        {**TOKEN, "access_token": 12},
        {**TOKEN, "access_token": "token\r\nInjected: secret"},
        {**TOKEN, "token_type": "MAC"},
        {**TOKEN, "token_type": None},
        {**TOKEN, "expires_in": True},
        {**TOKEN, "expires_in": "300"},
        {**TOKEN, "expires_in": 0},
        {**TOKEN, "expires_in": -1},
        {**TOKEN, "expires_in": 100_000},
        {**TOKEN, "scope": "tools:read admin"},
        {**TOKEN, "resource": "https://other.test"},
        {**TOKEN, "issuer": "https://other.test"},
    ],
)
@pytest.mark.asyncio
async def test_malformed_or_unbound_token_never_reaches_resource(monkeypatch, response):
    calls = []

    def issuer(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=response)

    auth = authentication(monkeypatch, issuer)
    client = HttpClient(authentication=auth, transport=httpx.MockTransport(issuer))
    with pytest.raises(ToolError) as error:
        await client.request("POST", RESOURCE, request_timeout=1, json={"business": True})
    assert calls == [ISSUER]
    assert error.value.details["stage"] == "authentication"
    assert "private-client-secret" not in str(error.value)
    assert "issued-token" not in json.dumps(error.value.as_dict())


@pytest.mark.parametrize(
    "body",
    [
        b'{"access_token":"x","access_token":"y"}',
        b'{"expires_in":NaN}',
        b'{"expires_in":Infinity}',
        b"\xff",
        b'"' + b"x" * 65_536 + b'"',
    ],
    ids=["duplicate-key", "nan", "infinity", "invalid-utf8", "oversized-body"],
)
@pytest.mark.asyncio
async def test_token_body_has_strict_json_utf8_and_size_bounds(monkeypatch, body):
    auth = authentication(
        monkeypatch,
        lambda _request: httpx.Response(
            200, content=body, headers={"Content-Type": "application/json"}
        ),
    )
    with pytest.raises(ToolError) as error:
        await auth.prepare(RESOURCE, remaining_timeout=1)
    assert error.value.message == "protocol authentication failed"


@pytest.mark.parametrize(
    "endpoint",
    ["https://other.test/mcp", "http://resource.test/mcp", RESOURCE + "?token=untrusted"],
)
@pytest.mark.asyncio
async def test_unapproved_resource_is_rejected_before_resolving_or_sending_credentials(
    monkeypatch, endpoint
):
    calls = []
    auth = authentication(
        monkeypatch, lambda request: calls.append(request) or httpx.Response(200, json=TOKEN)
    )
    monkeypatch.delenv("ASYNC_AUTH_SECRET")
    with pytest.raises(ToolError) as error:
        await auth.prepare(endpoint, remaining_timeout=1)
    assert error.value.details["reason"] == "resource_not_allowed" and calls == []


@pytest.mark.asyncio
async def test_redirect_and_changed_transport_cannot_reuse_a_cached_token(monkeypatch):
    calls = []
    auth = authentication(
        monkeypatch,
        lambda request: (
            calls.append(str(request.url))
            or httpx.Response(302, headers={"Location": "https://attacker.test/token"})
        ),
    )
    with pytest.raises(ToolError):
        await auth.prepare(RESOURCE, remaining_timeout=1)
    assert calls == [ISSUER]
    auth._token_http.transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json=TOKEN)
    )
    assert (await auth.prepare(RESOURCE)).headers["Authorization"] == "Bearer issued-token"
    auth._token_http.follow_redirects = True
    with pytest.raises(ToolError) as error:
        await auth.prepare(RESOURCE)
    assert error.value.details["reason"] == "issuer_transport_not_allowed"


@pytest.mark.asyncio
async def test_deadline_and_cancellation_close_auth_without_a_resource_request_or_token(
    monkeypatch,
):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    calls = []

    async def blocked(request):
        calls.append(str(request.url))
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    auth = authentication(monkeypatch, blocked)
    with pytest.raises(ToolError) as error:
        await auth.prepare(RESOURCE, remaining_timeout=0.03)
    assert error.value.details["reason"] == "deadline_expired" and cancelled.is_set()
    cancelled.clear()
    entered.clear()
    task = asyncio.create_task(auth.prepare(RESOURCE, remaining_timeout=1))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set() and calls == [ISSUER, ISSUER]
    assert auth._oauth2_provider._token is None


@pytest.mark.asyncio
async def test_concurrent_preparation_is_bounded_and_coalesces_one_token_request(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def issuer(request):
        calls.append(str(request.url))
        entered.set()
        await release.wait()
        return httpx.Response(200, json=TOKEN)

    auth = authentication(monkeypatch, issuer)
    tasks = [asyncio.create_task(auth.prepare(RESOURCE, remaining_timeout=2)) for _ in range(16)]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0)
        with pytest.raises(ToolError) as error:
            await auth.prepare(RESOURCE, remaining_timeout=1)
        assert error.value.details["reason"] == "preparation_capacity"
        release.set()
        values = await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert len(calls) == 1 and all(value.headers == values[0].headers for value in values)
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_sync_oauth_never_starts_token_io(monkeypatch):
    calls = []
    auth = authentication(
        monkeypatch, lambda request: calls.append(request) or httpx.Response(200, json=TOKEN)
    )
    with pytest.raises(ValueError, match="asynchronous"):
        auth.headers(RESOURCE)
    assert calls == []


@pytest.mark.asyncio
async def test_short_lived_token_is_not_cached_as_permanent_authority(monkeypatch):
    calls = []
    auth = authentication(
        monkeypatch,
        lambda request: (
            calls.append(request)
            or httpx.Response(
                200, json={**TOKEN, "access_token": f"token-{len(calls)}", "expires_in": 0.2}
            )
        ),
    )
    assert (await auth.prepare(RESOURCE)).headers["Authorization"] == "Bearer token-1"
    await asyncio.sleep(0.21)
    assert (await auth.prepare(RESOURCE)).headers["Authorization"] == "Bearer token-2"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_issuer_delay_and_resource_io_share_one_original_deadline(monkeypatch):
    calls = []
    resource_cancelled = asyncio.Event()
    # Mocked wire delays own this tiny budget. Build verified trust before its
    # clock starts; real TLS setup and certificate isolation have separate tests.
    context = httpx.create_ssl_context(verify=True, trust_env=False)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    client_type = httpx.AsyncClient

    def mock_client_with_prepared_trust(*args, **kwargs):
        assert isinstance(kwargs.get("transport"), httpx.MockTransport)
        assert kwargs.get("verify") is True and kwargs.get("cert") is None
        return client_type(*args, **{**kwargs, "verify": context})

    monkeypatch.setattr(httpx, "AsyncClient", mock_client_with_prepared_trust)

    async def delayed(request):
        calls.append(str(request.url))
        if str(request.url) == ISSUER:
            await asyncio.sleep(0.03)
            return httpx.Response(200, json=TOKEN)
        try:
            await asyncio.sleep(0.05)
            return httpx.Response(200, json={"completed": True})
        finally:
            resource_cancelled.set()

    auth = authentication(monkeypatch, delayed)
    client = HttpClient(authentication=auth, transport=httpx.MockTransport(delayed))
    with pytest.raises(ToolError):
        await client.request("POST", RESOURCE, request_timeout=0.06, json={})
    assert calls == [ISSUER, RESOURCE] and resource_cancelled.is_set()


@pytest.mark.parametrize(
    "update",
    [
        {"issuer": "https://untrusted.test"},
        {"resource": "http://resource.test"},
        {"allowed_resource_endpoints": ["https://resource.test/path?secret=not-allowed"]},
        {"scopes": ["tools:read admin"]},
        {"scopes": ["tools:read", "tools:read"]},
        {"token_timeout_seconds": float("inf")},
        {"max_token_response_bytes": 0},
    ],
)
def test_unbounded_or_untrusted_deployment_authentication_declaration_rejects(monkeypatch, update):
    with pytest.raises(ValueError):
        authentication(monkeypatch, lambda _request: httpx.Response(500), **update)


@pytest.mark.parametrize("kind", ["bearer", "api_key"])
@pytest.mark.asyncio
async def test_selected_static_header_binding_is_immutable_and_rejects_injection(monkeypatch, kind):
    monkeypatch.setenv("STATIC_AUTH_VALUE", "selected-private-value")
    profile = {
        "type": kind,
        "token" if kind == "bearer" else "key": {"from_env": "STATIC_AUTH_VALUE"},
    }
    auth = ProfileAuthentication(
        SecurityConfig.model_validate({"profiles": {"selected": profile}}), "selected"
    )
    binding = await auth.prepare(RESOURCE)
    assert dict(binding.headers) == dict(auth.headers(RESOURCE))
    monkeypatch.setenv("STATIC_AUTH_VALUE", "secret\r\nInjected: bad")
    with pytest.raises(ToolError) as error:
        await auth.prepare(RESOURCE)
    assert error.value.details["reason"] == "invalid_credential"
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("remaining", [0, -1, float("nan"), float("inf")])
@pytest.mark.asyncio
async def test_expired_or_nonfinite_budget_cannot_use_even_a_valid_cached_token(
    monkeypatch, remaining
):
    calls = []
    auth = authentication(
        monkeypatch, lambda request: calls.append(request) or httpx.Response(200, json=TOKEN)
    )
    await auth.prepare(RESOURCE, remaining_timeout=1)
    with pytest.raises(ToolError):
        await auth.prepare(RESOURCE, remaining_timeout=remaining)
    assert len(calls) == 1


def test_authentication_transport_can_bind_once_but_never_switch_policies(monkeypatch):
    monkeypatch.setenv("STATIC_AUTH_VALUE", "selected")
    security = SecurityConfig.model_validate(
        {"profiles": {"selected": {"type": "bearer", "token": {"from_env": "STATIC_AUTH_VALUE"}}}}
    )
    auth = ProfileAuthentication(security, "selected")
    first = HttpClient()
    auth.bind_transport(first)
    auth.bind_transport(first)
    with pytest.raises(ValueError, match="already bound"):
        auth.bind_transport(HttpClient())


@pytest.mark.asyncio
async def test_default_resource_is_the_exact_explicitly_approved_endpoint(monkeypatch):
    requests = []

    def issuer(request):
        requests.append(request)
        assert parse_qs(request.content.decode())["resource"] == [RESOURCE]
        return httpx.Response(200, json={**TOKEN, "resource": RESOURCE})

    auth = authentication(monkeypatch, issuer, resource=None)
    assert (await auth.prepare(RESOURCE)).headers["Authorization"] == "Bearer issued-token"
    assert len(requests) == 1
