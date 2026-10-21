from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, effect_request_digest
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

A2A_HEADERS = {"A2A-Version": "1.0"}
TASK_NOT_FOUND_INFO = {
    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
    "reason": "TASK_NOT_FOUND",
    "domain": "open-workflow-agent",
    "metadata": {},
}


def _runtime(tmp_path, *, transport: str, backend: str = "sqlite"):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake", "name": "fake/default"},
            "a2a": {"enabled": True, "transport": transport},
            "persistence": {"datasource": datasource},
        }
    )
    services = RuntimeServices(
        config,
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    assert services.invocations.connection.kind == backend
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


def _secured_runtime(
    tmp_path,
    *,
    transport: str,
    principal: str,
    token_env: str,
    workflow: dict[str, object] | None = None,
    skills: list[dict[str, str]] | None = None,
):
    config_values: dict[str, object] = {
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
            "skills": skills or [],
        },
    }
    if workflow is not None:
        config_values["workflow"] = workflow
    config = RuntimeConfig.model_validate(config_values)
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    return create_app(config=config, services=services), services


def _task_request(method: str, task_id: str, request_id: str = "task-1") -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {"id": task_id},
    }


async def _start_live_subscription(app, *, path: str, body: bytes, headers: dict[str, str]):
    """Start an ASGI SSE request without buffering it until the stream closes."""
    chunks: list[bytes] = []
    response_status: list[int] = []
    finished = asyncio.Event()
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            response_status.append(message["status"])
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                finished.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 123),
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
    }
    task = asyncio.create_task(app(scope, receive, send))

    def frames() -> list[dict[str, object]]:
        parsed = []
        for line in b"".join(chunks).splitlines():
            if not line.startswith(b"data: "):
                continue
            value = json.loads(line.removeprefix(b"data: ").decode("utf-8"))
            if isinstance(value, dict) and value.get("id") == "owner-subscribe":
                value = value.get("result", value)
            parsed.append(value)
        return parsed

    return task, frames, response_status


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
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"], ids=["sqlite", "postgresql"])
def test_cross_worker_terminal_task_read_preserves_uncertain_effect_without_replay(
    tmp_path, transport: str, backend: str
) -> None:
    """A stale worker's GetTask sees a peer commit without retrying uncertain work."""
    app, services = _runtime(tmp_path, transport=transport, backend=backend)
    handle = _create_handle(services)
    identity = EffectIdentity(
        invocation_id=handle.invocation_id,
        workflow_fingerprint=handle.workflow_fingerprint,
        task_reference="a2a-send",
        occurrence=(("a2a-send", 1),),
        operation="call:a2a",
    )
    request_digest = effect_request_digest("call:a2a", {"messageId": "uncertain-send"})
    pending = services.effect_journal.begin(identity, request_digest, "worker-one")
    assert pending.status == "pending"

    peer = RuntimeServices(
        services.config,
        model=FakeModel(),
        database_root=None if backend == "postgresql" else tmp_path,
    )
    try:
        peer_handle = peer.invocations.get(handle.invocation_id)
        assert peer_handle is not None and peer_handle.status == "running"
        peer.invocations.update(
            peer_handle,
            status="faulted",
            error={
                "code": "unresolved_external_action",
                "message": "remote operation outcome is uncertain",
            },
        )
        terminal = peer.invocations.get(handle.invocation_id)
        assert terminal is not None and terminal.status == "faulted"
        snapshot_after_peer_commit = terminal._persisted_payload

        async def forbidden(*_args, **_kwargs):
            raise AssertionError("cross-worker terminal GetTask must not replay workflow work")

        app.state.engine.invoke = forbidden
        app.state.engine.resume = forbidden
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

            first = get_task("cross-worker-terminal-first")
            second = get_task("cross-worker-terminal-second")

        assert first.status_code == second.status_code == 200
        first_body = first.json()
        second_body = second.json()
        first_task = first_body["result"] if transport == "jsonrpc" else first_body
        second_task = second_body["result"] if transport == "jsonrpc" else second_body
        assert first_task == second_task
        assert first_task["id"] == handle.invocation_id
        assert first_task["status"]["state"] == "TASK_STATE_FAILED"
        assert first_task["status"]["message"]["parts"][0]["text"] == (
            "workflow failed: unresolved_external_action"
        )
        assert services.invocations.get(handle.invocation_id)._persisted_payload == (
            snapshot_after_peer_commit
        )
        observed_receipt = services.effect_journal.get(identity)
        assert observed_receipt is not None and observed_receipt.status == "pending"
        assert services.model.calls == []
        assert peer.model.calls == []
    finally:
        peer.close()
        services.close()


@pytest.mark.parametrize("transport", ["jsonrpc", "http_json"])
@pytest.mark.parametrize(
    ("terminal", "expected_state"),
    [
        ("end", "TASK_STATE_COMPLETED"),
        ("deadline", "TASK_STATE_FAILED"),
    ],
)
def test_end_and_deadline_task_reads_are_authoritative_and_read_only(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    terminal: str,
    expected_state: str,
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

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("terminal GetTask must not resume workflow or perform network work")

    monkeypatch.setattr(app.state.engine, "invoke", forbidden)
    monkeypatch.setattr(app.state.engine, "resume", forbidden)
    monkeypatch.setattr(services.protocols, "call", forbidden)
    monkeypatch.setattr(services.protocols.http, "request", forbidden)

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


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["jsonrpc", "http_json"])
async def test_authenticated_a2a_creator_can_cancel_subscribe_and_resume_waiting_task(
    tmp_path, monkeypatch, transport: str
) -> None:
    monkeypatch.setenv("OWA_A2A_ALICE", "alice-secret")
    workflow = {
        "catalog": [
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "a2a-owner-tests",
                    "name": "owner-resume-flow",
                    "version": "1.0.0",
                },
                "do": [
                    {"pause": {"wait": {"seconds": 300}}},
                    {"mark": {"set": {"resumed": True}}},
                ],
            }
        ],
    }
    # The workflow skill lets the authenticated creator produce and resume a
    # persisted timer-wait task through the public A2A routes.
    app, services = _secured_runtime(
        tmp_path,
        transport=transport,
        principal="alice",
        token_env="OWA_A2A_ALICE",
        workflow=workflow,
        skills=[{"id": "echo", "workflow": "owner-resume-flow"}],
    )
    headers = {**A2A_HEADERS, "Authorization": "Bearer alice-secret"}
    subscribe_task = None

    try:
        async with app.router.lifespan_context(app):
            plan = app.state.a2a_skill_plans["echo"]
            cancellable = services.invocations.create(
                engine=app.state.engine.engine_name,
                session_id="owner-cancel-context",
                user_id=None,
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
                owner_principal="alice",
            )
            transport_client = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport_client, base_url="http://test"
            ) as client:
                if transport == "jsonrpc":
                    start = await client.post(
                        "/a2a",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": "owner-start",
                            "method": "SendMessage",
                            "params": {
                                "message": {
                                    "messageId": "owner-initial-message",
                                    "role": "ROLE_USER",
                                    "metadata": {"skillId": "echo"},
                                    "parts": [{"text": "start creator task"}],
                                }
                            },
                        },
                    )
                    assert start.status_code == 200, start.text
                    initial_task = start.json()["result"]["task"]
                    assert initial_task["id"]
                    waiting = services.invocations.get(initial_task["id"])
                    assert waiting is not None and waiting.status == "waiting"
                    assert waiting.owner_principal == "alice"
                    assert services.invocations.read_input(waiting) == {
                        "question": "start creator task"
                    }
                    assert initial_task["contextId"] == waiting.session_id
                    refreshed = await client.post(
                        "/a2a",
                        headers=headers,
                        json=_task_request("GetTask", waiting.invocation_id, "owner-get-waiting"),
                    )
                    assert refreshed.status_code == 200, refreshed.text
                    waiting_task = refreshed.json()["result"]
                    assert waiting_task["id"] == initial_task["id"]
                    assert waiting_task["contextId"] == initial_task["contextId"]
                    assert waiting_task["status"]["state"] == "TASK_STATE_WORKING"
                    assert waiting.invocation_id not in app.state.engine._active
                    assert waiting._execution_lease is None
                    assert not any(
                        event.invocation_id == waiting.invocation_id and event.task_name == "mark"
                        for event in services.events.events
                    )
                    cancel = await client.post(
                        "/a2a",
                        headers=headers,
                        json=_task_request("CancelTask", cancellable.invocation_id, "owner-cancel"),
                    )
                    (
                        subscribe_task,
                        subscribe_frames,
                        subscribe_status,
                    ) = await _start_live_subscription(
                        app,
                        path="/a2a",
                        body=json.dumps(
                            _task_request(
                                "SubscribeToTask", waiting.invocation_id, "owner-subscribe"
                            )
                        ).encode(),
                        headers={
                            **headers,
                            "content-type": "application/json",
                            "accept": "text/event-stream",
                        },
                    )
                    for _ in range(100):
                        if subscribe_frames():
                            break
                        await asyncio.sleep(0.01)
                    initial_frames = subscribe_frames()
                    assert initial_frames, "owner subscription did not receive the timer-wait task"
                    assert initial_frames[0]["task"]["id"] == waiting.invocation_id
                    assert initial_frames[0]["task"]["status"]["state"] == ("TASK_STATE_WORKING")
                    resume = await client.post(
                        "/a2a",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": "owner-resume",
                            "method": "SendMessage",
                            "params": {
                                "message": {
                                    "messageId": "owner-resume-message",
                                    "role": "ROLE_USER",
                                    "taskId": waiting.invocation_id,
                                    "contextId": waiting.session_id,
                                    "parts": [{"text": "continue as owner"}],
                                }
                            },
                        },
                    )
                else:
                    start = await client.post(
                        "/a2a/message:send",
                        headers=headers,
                        json={
                            "message": {
                                "messageId": "owner-initial-message",
                                "role": "ROLE_USER",
                                "metadata": {"skillId": "echo"},
                                "parts": [{"text": "start creator task"}],
                            }
                        },
                    )
                    assert start.status_code == 200, start.text
                    initial_task = start.json()["task"]
                    assert initial_task["id"]
                    waiting = services.invocations.get(initial_task["id"])
                    assert waiting is not None and waiting.status == "waiting"
                    assert waiting.owner_principal == "alice"
                    assert services.invocations.read_input(waiting) == {
                        "question": "start creator task"
                    }
                    assert initial_task["contextId"] == waiting.session_id
                    refreshed = await client.get(
                        f"/a2a/tasks/{waiting.invocation_id}", headers=headers
                    )
                    assert refreshed.status_code == 200, refreshed.text
                    waiting_task = refreshed.json()
                    assert waiting_task["id"] == initial_task["id"]
                    assert waiting_task["contextId"] == initial_task["contextId"]
                    assert waiting_task["status"]["state"] == "TASK_STATE_WORKING"
                    assert waiting.invocation_id not in app.state.engine._active
                    assert waiting._execution_lease is None
                    assert not any(
                        event.invocation_id == waiting.invocation_id and event.task_name == "mark"
                        for event in services.events.events
                    )
                    cancel = await client.post(
                        f"/a2a/tasks/{cancellable.invocation_id}:cancel", headers=headers
                    )
                    (
                        subscribe_task,
                        subscribe_frames,
                        subscribe_status,
                    ) = await _start_live_subscription(
                        app,
                        path=f"/a2a/tasks/{waiting.invocation_id}:subscribe",
                        body=b"",
                        headers={**headers, "accept": "text/event-stream"},
                    )
                    for _ in range(100):
                        if subscribe_frames():
                            break
                        await asyncio.sleep(0.01)
                    initial_frames = subscribe_frames()
                    assert initial_frames, "owner subscription did not receive the timer-wait task"
                    assert initial_frames[0]["task"]["id"] == waiting.invocation_id
                    assert initial_frames[0]["task"]["status"]["state"] == ("TASK_STATE_WORKING")
                    resume = await client.post(
                        "/a2a/message:send",
                        headers=headers,
                        json={
                            "message": {
                                "messageId": "owner-resume-message",
                                "role": "ROLE_USER",
                                "taskId": waiting.invocation_id,
                                "contextId": waiting.session_id,
                                "parts": [{"text": "continue as owner"}],
                            }
                        },
                    )

            await asyncio.wait_for(subscribe_task, timeout=15)
            assert subscribe_status == [200]

            assert cancel.status_code == 200, cancel.text
            cancel_payload = cancel.json()
            cancel_task = cancel_payload["result"] if transport == "jsonrpc" else cancel_payload
            assert cancel_task["status"]["state"] == "TASK_STATE_CANCELED"
            canceled = services.invocations.get(cancellable.invocation_id)
            assert canceled is not None and canceled.status == "cancelled"

            assert resume.status_code == 200, resume.text
            resume_payload = resume.json()
            resumed_task = (
                resume_payload["result"]["task"]
                if transport == "jsonrpc"
                else resume_payload["task"]
            )
            assert resumed_task["id"] == waiting.invocation_id
            assert resumed_task["contextId"] == waiting_task["contextId"]
            assert resumed_task["status"]["state"] == "TASK_STATE_COMPLETED"
            assert resumed_task["artifacts"][0]["parts"][0]["data"] == {"resumed": True}
            resumed = services.invocations.get(waiting.invocation_id)
            assert resumed is not None and resumed.status == "completed"
            assert resumed.owner_principal == "alice"

            frames = subscribe_frames()
            assert frames
            terminal_updates = [
                frame["statusUpdate"]
                for frame in frames
                if isinstance(frame, dict)
                and isinstance(frame.get("statusUpdate"), dict)
                and frame["statusUpdate"].get("status", {}).get("state")
                in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"}
            ]
            assert terminal_updates, frames
            assert terminal_updates[-1]["taskId"] == waiting.invocation_id
            assert terminal_updates[-1]["status"]["state"] == "TASK_STATE_COMPLETED"
    finally:
        if subscribe_task is not None and not subscribe_task.done():
            subscribe_task.cancel()
            await asyncio.gather(subscribe_task, return_exceptions=True)
        services.close()


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
