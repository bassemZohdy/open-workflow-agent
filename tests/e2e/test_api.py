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
