from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.api import (
    ApiAuthenticationMiddleware,
    RequestSizeLimitMiddleware,
    _is_limited_path,
)
from open_workflow_agent.config import RuntimeConfig


async def _ok_app(scope, receive, send):
    body = await receive()
    await receive()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body.get("body", b"ok")})


def test_request_paths_are_bounded_only_for_mutations():
    assert all(
        _is_limited_path(path)
        for path in (
            "/v1/invoke",
            "/v1/events",
            "/v1/schedules",
            "/v1/invocations/id/resume",
            "/v1/invocations/id/cancel",
            "/v1/schedules/id/cancel",
            "/v1/approvals/id/decision",
        )
    )
    assert not _is_limited_path("/v1/capabilities")
    assert not _is_limited_path("/v1/schedules/id")


@pytest.mark.asyncio
async def test_request_size_middleware_replays_buffered_body_and_rejects_stream_overflow():
    middleware = RequestSizeLimitMiddleware(_ok_app, max_bytes=4)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        accepted = await client.post("/v1/events", content=b"four")
        assert accepted.status_code == 200
        assert accepted.content == b"four"

        overflow = await client.post("/v1/events", content=b"five!")
        assert overflow.status_code == 413
        assert overflow.json()["error"]["code"] == "request_too_large"

        bypass = await client.post("/v1/capabilities", content=b"five!")
        assert bypass.status_code == 200


@pytest.mark.asyncio
async def test_request_size_middleware_rejects_chunked_overflow():
    middleware = RequestSizeLimitMiddleware(_ok_app, max_bytes=4)
    scope = {
        "type": "http",
        "path": "/v1/events",
        "method": "POST",
        "headers": [],
        "query_string": b"",
    }
    messages = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"de", "more_body": False},
        ]
    )
    sent: list[dict[str, object]] = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_api_authentication_middleware_profiles_and_bypass(monkeypatch):
    monkeypatch.setenv("OWA_BOUNDARY_TOKEN", "token")
    monkeypatch.setenv("OWA_BOUNDARY_KEY", "key")
    bearer_config = RuntimeConfig.model_validate(
        {
            "security": {
                "profiles": {
                    "bearer": {"type": "bearer", "token": {"from_env": "OWA_BOUNDARY_TOKEN"}}
                }
            }
        }
    )
    key_config = RuntimeConfig.model_validate(
        {
            "security": {
                "profiles": {
                    "key": {
                        "type": "api_key",
                        "key": {"from_env": "OWA_BOUNDARY_KEY"},
                        "header": "X-API-Key",
                    }
                }
            }
        }
    )
    bearer = ApiAuthenticationMiddleware(
        _ok_app, security=bearer_config.security, profile_name="bearer"
    )
    key = ApiAuthenticationMiddleware(_ok_app, security=key_config.security, profile_name="key")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=bearer), base_url="http://test"
    ) as client:
        assert (await client.get("/health/live")).status_code == 200
        assert (await client.get("/v1/capabilities")).status_code == 401
        assert (
            await client.get("/v1/capabilities", headers={"Authorization": "Bearer wrong"})
        ).status_code == 403
        assert (
            await client.get("/v1/capabilities", headers={"Authorization": "Bearer token"})
        ).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=key), base_url="http://test"
    ) as client:
        assert (await client.get("/v1/capabilities")).status_code == 401
        assert (
            await client.get("/v1/capabilities", headers={"X-API-Key": "wrong"})
        ).status_code == 403
        assert (
            await client.get("/v1/capabilities", headers={"X-API-Key": "key"})
        ).status_code == 200
