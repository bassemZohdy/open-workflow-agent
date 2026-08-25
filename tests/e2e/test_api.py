from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices


@pytest.mark.asyncio
async def test_invoke_capabilities_health_and_reload(tmp_path):
    services = RuntimeServices(
        RuntimeConfig(), model=FakeModel({"response": "hello"}), database_root=tmp_path
    )
    app = create_app(config=RuntimeConfig(), services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health/live")).json() == {"status": "ok"}
            capabilities = (await client.get("/v1/capabilities")).json()
            assert capabilities["workflowDsl"] == "1.0.3"
            result = await client.post("/v1/invoke", json={"input": {"question": "hi"}})
            assert result.status_code == 200
            assert result.json()["status"] == "completed"
            assert (await client.post("/v1/admin/knowledge/reload")).status_code == 200


@pytest.mark.asyncio
async def test_api_limits_payloads_and_normalizes_not_found(tmp_path):
    config = RuntimeConfig.model_validate(
        {"model": {"provider": "fake"}, "server": {"max_request_bytes": 32}}
    )
    services = RuntimeServices(
        config, model=FakeModel({"response": "hello"}), database_root=tmp_path
    )
    app = create_app(config=config, services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health/ready")).json() == {"status": "ok"}
            oversized = await client.post("/v1/invoke", json={"input": "x" * 100})
            assert oversized.status_code == 413
            assert oversized.json()["error"]["code"] == "request_too_large"
            missing = await client.post("/v1/invocations/missing/resume", json={"input": {}})
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "invocation_not_found"
            malformed = await client.post("/v1/invoke", json={"unexpected": True})
            assert malformed.status_code == 422
            assert malformed.json()["error"]["code"] == "request_validation_error"
