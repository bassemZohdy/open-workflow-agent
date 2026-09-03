from __future__ import annotations

from fastapi.testclient import TestClient
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import A2AConfig, RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

A2A_HEADERS = {"A2A-Version": "1.0"}


def _runtime(tmp_path, *, transport: str):
    config = RuntimeConfig(
        model={"provider": "fake", "name": "fake/default"},
        a2a=A2AConfig(enabled=True, transport=transport),
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    app = create_app(config=config, services=services)
    return app, services


def _create_handle(services, *, status: str = "running", output=None):
    plan = compile_workflow()
    handle = services.invocations.create(
        engine="portable",
        session_id="ctx-1",
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    if status != "running" or output is not None:
        services.invocations.update(handle, status=status, output=output)
    return handle


def test_jsonrpc_get_task_projects_common_invocation_state(tmp_path) -> None:
    app, services = _runtime(tmp_path, transport="jsonrpc")
    handle = _create_handle(services, status="completed", output={"ok": True})

    with TestClient(app) as client:
        response = client.post(
            "/a2a",
            headers=A2A_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": "get-1",
                "method": "GetTask",
                "params": {"id": handle.invocation_id},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "get-1"
    assert payload["result"]["id"] == handle.invocation_id
    assert payload["result"]["contextId"] == "ctx-1"
    assert payload["result"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "engine_execution_reference" not in response.text


def test_jsonrpc_task_not_found_uses_official_error_code_and_request_id(tmp_path) -> None:
    app, _services = _runtime(tmp_path, transport="jsonrpc")

    with TestClient(app) as client:
        response = client.post(
            "/a2a",
            headers=A2A_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": "missing-1",
                "method": "GetTask",
                "params": {"id": "missing"},
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "missing-1",
        "error": {"code": -32001, "message": "task not found"},
    }


def test_jsonrpc_cancel_task_uses_common_engine_cancellation(tmp_path) -> None:
    app, services = _runtime(tmp_path, transport="jsonrpc")
    handle = _create_handle(services)

    with TestClient(app) as client:
        response = client.post(
            "/a2a",
            headers=A2A_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": "cancel-1",
                "method": "CancelTask",
                "params": {"id": handle.invocation_id},
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert services.invocations.get(handle.invocation_id).status == "cancelled"


def test_jsonrpc_completed_task_is_not_cancelable(tmp_path) -> None:
    app, services = _runtime(tmp_path, transport="jsonrpc")
    handle = _create_handle(services, status="completed", output="done")

    with TestClient(app) as client:
        response = client.post(
            "/a2a",
            headers=A2A_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": "cancel-terminal",
                "method": "CancelTask",
                "params": {"id": handle.invocation_id},
            },
        )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32002
    assert response.json()["id"] == "cancel-terminal"


def test_http_json_get_and_cancel_task_use_official_paths(tmp_path) -> None:
    app, services = _runtime(tmp_path, transport="http_json")
    completed = _create_handle(services, status="completed", output="done")
    running = _create_handle(services)

    with TestClient(app) as client:
        get_response = client.get(
            f"/a2a/tasks/{completed.invocation_id}",
            headers=A2A_HEADERS,
        )
        cancel_response = client.post(
            f"/a2a/tasks/{running.invocation_id}:cancel",
            headers=A2A_HEADERS,
        )
        missing_response = client.get("/a2a/tasks/missing", headers=A2A_HEADERS)

    assert get_response.status_code == 200
    assert get_response.headers["content-type"].startswith("application/a2a+json")
    assert get_response.json()["status"]["state"] == "TASK_STATE_COMPLETED"
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"]["state"] == "TASK_STATE_CANCELED"
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "task_not_found"


def test_capabilities_advertise_only_implemented_task_operations(tmp_path) -> None:
    app, _services = _runtime(tmp_path, transport="jsonrpc")

    with TestClient(app) as client:
        capabilities = client.get("/v1/capabilities").json()["features"]["a2a"]

    assert capabilities["tasks"] is True
    assert capabilities["taskOperations"] == ["GetTask", "CancelTask"]
    assert capabilities["streaming"] is True
    assert capabilities["streamingOperations"] == ["SendStreamingMessage", "SubscribeToTask"]
    assert capabilities["pushNotifications"] is False
