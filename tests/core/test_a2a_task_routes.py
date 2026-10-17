from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import A2AConfig, RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

A2A_HEADERS = {"A2A-Version": "1.0"}
TASK_NOT_FOUND_INFO = {
    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
    "reason": "TASK_NOT_FOUND",
    "domain": "open-workflow-agent",
    "metadata": {},
}


def _runtime(tmp_path, *, transport: str):
    config = RuntimeConfig(
        model={"provider": "fake", "name": "fake/default"},
        a2a=A2AConfig(enabled=True, transport=transport),
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    app = create_app(config=config, services=services)
    return app, services


def _create_handle(services, *, status: str = "running", output=None, owner_principal=None):
    plan = compile_workflow()
    handle = services.invocations.create(
        engine="portable",
        session_id="ctx-1",
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
        owner_principal=owner_principal,
    )
    if status != "running" or output is not None:
        services.invocations.update(handle, status=status, output=output)
    return handle


def _secured_runtime(tmp_path, *, transport: str, principal: str, token_env: str):
    config = RuntimeConfig.model_validate(
        {
            "security": {
                "profiles": {
                    "a2a-caller": {
                        "type": "bearer",
                        "token": {"from_env": token_env},
                        "principal": principal,
                    }
                }
            },
            "a2a": {
                "enabled": True,
                "transport": transport,
                "security_profile": "a2a-caller",
            },
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    return create_app(config=config, services=services), services


def _task_request(method: str, task_id: str, request_id: str = "task-1") -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {"id": task_id},
    }


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
        "error": {"code": -32001, "message": "task not found", "data": [TASK_NOT_FOUND_INFO]},
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
    assert missing_response.json()["error"]["details"][0]["metadata"]["owaCode"] == "task_not_found"


@pytest.mark.parametrize("transport", ["jsonrpc", "http_json"])
def test_cancelled_task_get_projection_is_stable_and_read_only(tmp_path, transport: str) -> None:
    app, services = _runtime(tmp_path, transport=transport)
    handle = _create_handle(services)

    with TestClient(app) as client:
        if transport == "jsonrpc":
            cancel_response = client.post(
                "/a2a",
                headers=A2A_HEADERS,
                json=_task_request("CancelTask", handle.invocation_id, "cancel-readonly"),
            )

            def get_task(request_id: str):
                return client.post(
                    "/a2a",
                    headers=A2A_HEADERS,
                    json=_task_request("GetTask", handle.invocation_id, request_id),
                )

        else:
            cancel_response = client.post(
                f"/a2a/tasks/{handle.invocation_id}:cancel",
                headers=A2A_HEADERS,
            )

            def get_task(_request_id: str):
                return client.get(
                    f"/a2a/tasks/{handle.invocation_id}",
                    headers=A2A_HEADERS,
                )

        assert cancel_response.status_code == 200, cancel_response.text
        snapshot_after_cancel = services.invocations.get(handle.invocation_id)._persisted_payload

        first = get_task("get-cancelled-first")
        second = get_task("get-cancelled-second")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_body = first.json()
    second_body = second.json()
    first_task = first_body["result"] if transport == "jsonrpc" else first_body
    second_task = second_body["result"] if transport == "jsonrpc" else second_body
    assert first_task["id"] == second_task["id"] == handle.invocation_id
    assert first_task["contextId"] == second_task["contextId"] == "ctx-1"
    assert first_task["status"]["state"] == second_task["status"]["state"] == "TASK_STATE_CANCELED"
    assert (
        services.invocations.get(handle.invocation_id)._persisted_payload == snapshot_after_cancel
    )


@pytest.mark.parametrize("transport", ["jsonrpc", "http_json"])
@pytest.mark.parametrize(
    ("terminal", "expected_state"),
    [
        ("end", "TASK_STATE_COMPLETED"),
        ("deadline", "TASK_STATE_FAILED"),
    ],
)
def test_end_and_deadline_task_reads_are_authoritative_and_read_only(
    tmp_path, transport: str, terminal: str, expected_state: str
) -> None:
    app, services = _runtime(tmp_path, transport=transport)
    handle = _create_handle(services)
    if terminal == "end":
        services.invocations.update(handle, status="completed", output={"ended": True})
    else:
        services.invocations.update(
            handle,
            status="faulted",
            error={"code": "workflow_deadline_exceeded"},
        )
    snapshot_after_terminal = services.invocations.get(handle.invocation_id)._persisted_payload

    with TestClient(app) as client:
        if transport == "jsonrpc":

            def get_task(request_id: str):
                return client.post(
                    "/a2a",
                    headers=A2A_HEADERS,
                    json=_task_request("GetTask", handle.invocation_id, request_id),
                )

        else:

            def get_task(_request_id: str):
                return client.get(
                    f"/a2a/tasks/{handle.invocation_id}",
                    headers=A2A_HEADERS,
                )

        first = get_task("get-terminal-first")
        second = get_task("get-terminal-second")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_body = first.json()
    second_body = second.json()
    first_task = first_body["result"] if transport == "jsonrpc" else first_body
    second_task = second_body["result"] if transport == "jsonrpc" else second_body
    assert first_task == second_task
    assert first_task["id"] == handle.invocation_id
    assert first_task["contextId"] == "ctx-1"
    assert first_task["status"]["state"] == expected_state
    if terminal == "end":
        assert first_task["artifacts"][0]["parts"][0]["data"] == {"ended": True}
    else:
        assert first_task["status"]["message"]["parts"][0]["text"] == (
            "workflow failed: workflow_deadline_exceeded"
        )
    assert (
        services.invocations.get(handle.invocation_id)._persisted_payload == snapshot_after_terminal
    )


@pytest.mark.parametrize("transport", ["jsonrpc", "http_json"])
def test_actual_workflow_deadline_projects_stable_read_only_task(
    tmp_path, monkeypatch, transport: str
) -> None:
    config = RuntimeConfig.model_validate(
        {
            "workflow": {
                "definition": {
                    "document": {
                        "dsl": "1.0.3",
                        "namespace": "a2a-tests",
                        "name": "deadline-projection",
                        "version": "1.0.0",
                    },
                    "timeout": {"after": {"seconds": 1}},
                    "do": [
                        {
                            "model": {
                                "call": "llm:1.0.0@default",
                                "with": {"prompt": "must not run after deadline"},
                            }
                        }
                    ],
                }
            },
            "a2a": {"enabled": True, "transport": transport},
        }
    )
    model = FakeModel()
    services = RuntimeServices(config, model=model, database_root=tmp_path)
    app = create_app(config=config, services=services)
    message = {
        "messageId": "deadline-message",
        "role": "ROLE_USER",
        "parts": [{"text": "start deadline workflow"}],
    }

    with TestClient(app) as client:
        absolute_time = [1000.0]

        def advance_workflow_clock() -> float:
            current = absolute_time[0]
            absolute_time[0] += 2.0
            return current

        monkeypatch.setattr(app.state.engine.executor, "_absolute_clock", advance_workflow_clock)
        if transport == "jsonrpc":
            send = client.post(
                "/a2a",
                headers=A2A_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": "deadline-send",
                    "method": "SendMessage",
                    "params": {"message": message},
                },
            )

            def get_task(request_id: str):
                return client.post(
                    "/a2a",
                    headers=A2A_HEADERS,
                    json=_task_request("GetTask", task_id, request_id),
                )

        else:
            send = client.post(
                "/a2a/message:send",
                headers=A2A_HEADERS,
                json={"message": message},
            )

            def get_task(_request_id: str):
                return client.get(f"/a2a/tasks/{task_id}", headers=A2A_HEADERS)

        assert send.status_code == 200, send.text
        send_body = send.json()
        if transport == "jsonrpc":
            assert send_body["id"] == "deadline-send"
        task_id = (
            send_body["result"]["task"]["id"] if transport == "jsonrpc" else send_body["task"]["id"]
        )
        handle = services.invocations.get(task_id)
        assert handle is not None
        first = get_task("deadline-first")
        second = get_task("deadline-second")
        terminal_handle = services.invocations.get(task_id)
        assert terminal_handle is not None
        assert terminal_handle.status == "faulted"
        assert terminal_handle.error is not None
        assert terminal_handle.error["message"] == "workflow timed out", terminal_handle.error
        snapshot_after_deadline = terminal_handle._persisted_payload
        model_calls_after_deadline = list(model.calls)

    assert first.status_code == second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    first_task = first_body["result"] if transport == "jsonrpc" else first_body
    second_task = second_body["result"] if transport == "jsonrpc" else second_body
    assert first_task == second_task
    assert first_task["id"] == task_id
    assert first_task["contextId"] == terminal_handle.session_id
    assert first_task["status"]["state"] == "TASK_STATE_FAILED"
    assert first_task["status"]["message"]["parts"][0]["text"] == (
        "workflow failed: workflow_execution_error"
    )
    assert model_calls_after_deadline == []
    assert model.calls == model_calls_after_deadline
    assert services.invocations.get(task_id)._persisted_payload == snapshot_after_deadline


def test_capabilities_advertise_only_implemented_task_operations(tmp_path) -> None:
    app, _services = _runtime(tmp_path, transport="jsonrpc")

    with TestClient(app) as client:
        capabilities = client.get("/v1/capabilities").json()["features"]["a2a"]

    assert capabilities["tasks"] is True
    assert capabilities["taskOperations"] == ["GetTask", "CancelTask"]
    assert capabilities["streaming"] is True
    assert capabilities["streamingOperations"] == ["SendStreamingMessage", "SubscribeToTask"]
    assert capabilities["pushNotifications"] is False


@pytest.mark.parametrize("transport", ["jsonrpc", "http_json"])
def test_authenticated_a2a_task_access_is_bound_to_creator(
    tmp_path, monkeypatch, transport
) -> None:
    monkeypatch.setenv("OWA_A2A_ALICE", "alice-secret")
    monkeypatch.setenv("OWA_A2A_BOB", "bob-secret")

    alice_app, alice_services = _secured_runtime(
        tmp_path, transport=transport, principal="alice", token_env="OWA_A2A_ALICE"
    )
    handle = _create_handle(alice_services, owner_principal="alice")
    unknown_id = "unknown-task-id"
    owner_payload_before = alice_services.invocations.get(handle.invocation_id)._persisted_payload
    owner_model_calls_before = list(alice_services.model.calls)

    alice_headers = {**A2A_HEADERS, "Authorization": "Bearer alice-secret"}
    bob_app, bob_services = _secured_runtime(
        tmp_path, transport=transport, principal="bob", token_env="OWA_A2A_BOB"
    )
    try:
        with TestClient(alice_app) as alice_client:
            if transport == "jsonrpc":
                response = alice_client.post(
                    "/a2a",
                    headers=alice_headers,
                    json=_task_request("GetTask", handle.invocation_id),
                )
                assert response.json()["result"]["id"] == handle.invocation_id
            else:
                response = alice_client.get(
                    f"/a2a/tasks/{handle.invocation_id}", headers=alice_headers
                )
                assert response.status_code == 200
                assert response.json()["id"] == handle.invocation_id

        bob_headers = {**A2A_HEADERS, "Authorization": "Bearer bob-secret"}
        with TestClient(bob_app) as bob_client:
            if transport == "jsonrpc":
                for method in ("GetTask", "CancelTask", "SubscribeToTask"):
                    response = bob_client.post(
                        "/a2a",
                        headers=bob_headers,
                        json=_task_request(method, handle.invocation_id, method),
                    )
                    assert response.status_code == 200
                    assert response.json()["error"] == {
                        "code": -32001,
                        "message": "task not found",
                        "data": [TASK_NOT_FOUND_INFO],
                    }
                    unknown = bob_client.post(
                        "/a2a",
                        headers=bob_headers,
                        json=_task_request(method, unknown_id, method),
                    )
                    assert unknown.status_code == response.status_code
                    assert unknown.json() == response.json()
                resume = bob_client.post(
                    "/a2a",
                    headers=bob_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": "resume-1",
                        "method": "SendMessage",
                        "params": {
                            "message": {
                                "messageId": "foreign-resume",
                                "role": "ROLE_USER",
                                "taskId": handle.invocation_id,
                                "parts": [{"text": "continue"}],
                            }
                        },
                    },
                )
                unknown_resume = bob_client.post(
                    "/a2a",
                    headers=bob_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": "resume-1",
                        "method": "SendMessage",
                        "params": {
                            "message": {
                                "messageId": "foreign-resume",
                                "role": "ROLE_USER",
                                "taskId": unknown_id,
                                "parts": [{"text": "continue"}],
                            }
                        },
                    },
                )
                assert resume.json()["error"] == {
                    "code": -32001,
                    "message": "task not found",
                    "data": [TASK_NOT_FOUND_INFO],
                }
                assert unknown_resume.status_code == resume.status_code
                assert unknown_resume.json() == resume.json()
            else:
                get_response = bob_client.get(
                    f"/a2a/tasks/{handle.invocation_id}", headers=bob_headers
                )
                cancel_response = bob_client.post(
                    f"/a2a/tasks/{handle.invocation_id}:cancel", headers=bob_headers
                )
                subscribe_response = bob_client.post(
                    f"/a2a/tasks/{handle.invocation_id}:subscribe", headers=bob_headers
                )
                resume = bob_client.post(
                    "/a2a/message:send",
                    headers=bob_headers,
                    json={
                        "message": {
                            "messageId": "foreign-resume",
                            "role": "ROLE_USER",
                            "taskId": handle.invocation_id,
                            "parts": [{"text": "continue"}],
                        }
                    },
                )
                unknown_get = bob_client.get(f"/a2a/tasks/{unknown_id}", headers=bob_headers)
                unknown_cancel = bob_client.post(
                    f"/a2a/tasks/{unknown_id}:cancel", headers=bob_headers
                )
                unknown_subscribe = bob_client.post(
                    f"/a2a/tasks/{unknown_id}:subscribe", headers=bob_headers
                )
                unknown_resume = bob_client.post(
                    "/a2a/message:send",
                    headers=bob_headers,
                    json={
                        "message": {
                            "messageId": "foreign-resume",
                            "role": "ROLE_USER",
                            "taskId": unknown_id,
                            "parts": [{"text": "continue"}],
                        }
                    },
                )
                assert get_response.status_code == 404
                assert cancel_response.status_code == 404
                assert subscribe_response.status_code == 404
                assert resume.status_code == 404
                assert get_response.json() == {
                    "error": {
                        "code": 5,
                        "message": "task not found",
                        "details": [
                            {**TASK_NOT_FOUND_INFO, "metadata": {"owaCode": "task_not_found"}}
                        ],
                    }
                }
                assert cancel_response.json() == get_response.json()
                assert subscribe_response.json() == get_response.json()
                assert resume.json() == get_response.json()
                assert unknown_get.status_code == get_response.status_code
                assert unknown_cancel.status_code == cancel_response.status_code
                assert unknown_subscribe.status_code == subscribe_response.status_code
                assert unknown_resume.status_code == resume.status_code
                assert unknown_get.json() == get_response.json()
                assert unknown_cancel.json() == cancel_response.json()
                assert unknown_subscribe.json() == subscribe_response.json()
                assert unknown_resume.json() == resume.json()
            assert bob_services.invocations.get(handle.invocation_id).status == "running"
            assert (
                alice_services.invocations.get(handle.invocation_id)._persisted_payload
                == owner_payload_before
            )
            assert alice_services.model.calls == owner_model_calls_before
            assert bob_services.model.calls == []
    finally:
        # TestClient closes the application services; this also releases the
        # shared SQLite handle for the next test.
        alice_services.close()
        bob_services.close()


def test_authenticated_a2a_start_persists_creator_identity(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OWA_A2A_ALICE", "alice-secret")
    app, services = _secured_runtime(
        tmp_path, transport="jsonrpc", principal="alice", token_env="OWA_A2A_ALICE"
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/a2a",
                headers={**A2A_HEADERS, "Authorization": "Bearer alice-secret"},
                json={
                    "jsonrpc": "2.0",
                    "id": "start-1",
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "messageId": "start-message",
                            "role": "ROLE_USER",
                            "parts": [{"text": "hello"}],
                        },
                        "configuration": {"returnImmediately": True},
                    },
                },
            )
        task_id = response.json()["result"]["task"]["id"]
        assert services.invocations.get(task_id).owner_principal == "alice"
    finally:
        services.close()
