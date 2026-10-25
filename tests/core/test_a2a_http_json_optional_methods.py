"""HTTP+JSON must reject optional A2A v1 operations before dispatch."""

from __future__ import annotations

import httpx
import pytest
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices

_UNIMPLEMENTED_OPERATIONS = [
    pytest.param("GET", "/a2a/tasks", None, id="list-tasks"),
    pytest.param("GET", "/a2a/extendedAgentCard", None, id="extended-agent-card"),
    pytest.param(
        "POST",
        "/a2a/tasks/task-id/pushNotificationConfigs",
        {},
        id="create-push-config",
    ),
    pytest.param(
        "GET",
        "/a2a/tasks/task-id/pushNotificationConfigs/config-id",
        None,
        id="get-push-config",
    ),
    pytest.param(
        "GET",
        "/a2a/tasks/task-id/pushNotificationConfigs",
        None,
        id="list-push-configs",
    ),
    pytest.param(
        "DELETE",
        "/a2a/tasks/task-id/pushNotificationConfigs/config-id",
        None,
        id="delete-push-config",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "payload"), _UNIMPLEMENTED_OPERATIONS)
async def test_http_json_optional_operations_reject_before_workflow_dispatch(
    tmp_path, method: str, path: str, payload: dict[str, object] | None
) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True, "transport": "http_json"}})
    model = FakeModel({"response": "must-not-run"})
    services = RuntimeServices(config, model=model, database_root=tmp_path)
    app = create_app(config=config, services=services)

    async with app.router.lifespan_context(app):
        before = services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocations"
        ).fetchone()[0]
        transport = httpx.ASGITransport(app=app)
        headers = {"A2A-Version": A2A_PROTOCOL_VERSION}
        if payload is not None:
            headers["Content-Type"] = "application/a2a+json"

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, path, headers=headers, json=payload)

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"detail": "Not Found"}
        assert (
            services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[
                0
            ]
            == before
        )
        assert model.calls == []
