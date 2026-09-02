"""Protocol-native async behavior for the bounded inbound A2A profile.

Covers ``SendMessageConfiguration.returnImmediately``, Task-returning sends,
and resuming sends over the common resume contract (A2A-6).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION as V1
from open_workflow_agent.api import create_app
from open_workflow_agent.approvals import APPROVAL_DECISION_EVENT, APPROVAL_REQUEST_EVENT
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices

HEADERS = {"a2a-version": V1}


def _send(message: dict[str, object], **params: object) -> dict[str, object]:
    body: dict[str, object] = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage"}
    payload: dict[str, object] = {"message": message}
    payload.update(params)
    body["params"] = payload
    return body


def _text_message(text: str = "hello", **extra: object) -> dict[str, object]:
    message: dict[str, object] = {"role": "ROLE_USER", "parts": [{"text": text}]}
    message.update(extra)
    return message


def _make_app(tmp_path, config: RuntimeConfig):
    services = RuntimeServices(
        config, model=FakeModel({"response": "a2a-reply"}), database_root=tmp_path
    )
    app = create_app(config=config, services=services)
    return app, services


async def _poll_task_state(
    client: httpx.AsyncClient,
    task_id: str,
    state: str,
    attempts: int = 60,
    transport: str = "jsonrpc",
) -> dict[str, object]:
    for _ in range(attempts):
        if transport == "http_json":
            response = await client.get(f"/a2a/tasks/{task_id}", headers=HEADERS)
            assert response.status_code == 200, response.text
            result = response.json()
        else:
            response = await client.post(
                "/a2a",
                headers=HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "GetTask",
                    "params": {"id": task_id},
                },
            )
            assert response.status_code == 200, response.text
            result = response.json()["result"]
        if result["status"]["state"] == state:
            return result
        await asyncio.sleep(0.05)
    raise AssertionError(f"task did not reach {state}: {result}")


@pytest.mark.asyncio
async def test_return_immediately_returns_task_and_completes(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app, _services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/a2a",
                headers=HEADERS,
                json=_send(_text_message(), configuration={"returnImmediately": True}),
            )
            assert response.status_code == 200
            body = response.json()["result"]
            assert "task" in body
            assert "message" not in body
            task_id = body["task"]["id"]

            final = await _poll_task_state(client, task_id, "TASK_STATE_COMPLETED")
            assert final["artifacts"][0]["parts"][0]["data"] == {"response": "a2a-reply"}


@pytest.mark.asyncio
async def test_return_immediately_is_opt_in_and_defaults_to_blocking(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app, _services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            blocking = await client.post("/a2a", headers=HEADERS, json=_send(_text_message()))
            assert blocking.status_code == 200
            assert "message" in blocking.json()["result"]


@pytest.mark.asyncio
async def test_approval_wait_projects_input_required_and_completes(tmp_path) -> None:
    config = RuntimeConfig.model_validate(
        {
            "approvals": {"enabled": True, "operator_security_profile": "operator"},
            "security": {
                "profiles": {
                    "operator": {"type": "bearer", "token": {"from_env": "OWA_TEST_OPERATOR"}}
                }
            },
            "workflow": {
                "catalog": [
                    {
                        "document": {
                            "dsl": "1.0.3",
                            "namespace": "a2a-async",
                            "name": "approval-flow",
                            "version": "1.0.0",
                        },
                        "do": [
                            {
                                "approval": {
                                    "listen": {
                                        "to": {
                                            "one": {
                                                "with": {
                                                    "type": APPROVAL_DECISION_EVENT,
                                                    "subject": "approval-a2a-async-1",
                                                }
                                            }
                                        },
                                        "read": "data",
                                    }
                                }
                            },
                            {"finish": {"set": {"approved": True}}},
                        ],
                    }
                ]
            },
            "a2a": {
                "enabled": True,
                "skills": [{"id": "approve", "workflow": "approval-flow"}],
            },
        }
    )
    app, services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = await client.post(
                "/a2a",
                headers=HEADERS,
                json=_send(
                    _text_message(metadata={"skillId": "approve"}),
                    configuration={"returnImmediately": True},
                ),
            )
            assert started.status_code == 200
            task_id = started.json()["result"]["task"]["id"]

            await _poll_task_state(client, task_id, "TASK_STATE_INPUT_REQUIRED")

            await services.event_bus.publish(
                {
                    "id": "approval-a2a-async-1",
                    "subject": "approval-a2a-async-1",
                    "type": APPROVAL_REQUEST_EVENT,
                    "data": {"question": "continue?"},
                },
                default_source="urn:a2a-async",
            )
            await services.approvals.decide(
                "approval-a2a-async-1",
                decision="approved",
                operator_id="operator-1",
                value={"approved": True},
                operation_key="approval-a2a-async-1",
            )

            final = await _poll_task_state(client, task_id, "TASK_STATE_COMPLETED")
            assert final["artifacts"][0]["parts"][0]["data"] == {"approved": True}


@pytest.mark.asyncio
async def test_resuming_send_on_unknown_task_fails_closed(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app, _services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/a2a",
                headers=HEADERS,
                json=_send(_text_message(taskId="missing-task")),
            )
            assert response.status_code == 200
            error = response.json()["error"]
            assert error["code"] == -32001


@pytest.mark.asyncio
async def test_resuming_send_on_terminal_task_is_rejected(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True}})
    app, services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        plan = app.state.plan
        handle = services.invocations.create(
            engine="adk",
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        services.invocations.update(handle, status="completed", output={"done": True})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/a2a",
                headers=HEADERS,
                json=_send(_text_message(taskId=handle.invocation_id)),
            )
            assert response.status_code == 200
            error = response.json()["error"]
            assert error["code"] == -32000
            assert error["message"] == "task is not accepting input"


@pytest.mark.asyncio
async def test_resuming_send_restarts_persisted_waiting_task(tmp_path) -> None:
    config = RuntimeConfig.model_validate(
        {
            "workflow": {
                "catalog": [
                    {
                        "document": {
                            "dsl": "1.0.3",
                            "namespace": "a2a-async",
                            "name": "echo-flow",
                            "version": "1.0.0",
                        },
                        "do": [{"mark": {"set": {"resumed": True}}}],
                    }
                ]
            },
            "a2a": {
                "enabled": True,
                "skills": [{"id": "echo", "workflow": "echo-flow"}],
            },
        }
    )
    app, services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        plan = app.state.a2a_skill_plans["echo"]
        handle = services.invocations.create(
            engine="adk",
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        services.invocations.update(handle, status="waiting")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/a2a",
                headers=HEADERS,
                json=_send(_text_message("continue", taskId=handle.invocation_id)),
            )
            assert response.status_code == 200
            body = response.json()["result"]
            assert body["message"]["parts"][0]["text"] == '{"resumed": true}'

            final = await _poll_task_state(client, handle.invocation_id, "TASK_STATE_COMPLETED")
            assert final["id"] == handle.invocation_id


@pytest.mark.asyncio
async def test_http_json_return_immediately_and_resume_rejections(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"a2a": {"enabled": True, "transport": "http_json"}})
    app, services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        plan = app.state.plan
        handle = services.invocations.create(
            engine="adk",
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        services.invocations.update(handle, status="completed", output={"done": True})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.post(
                "/a2a/message:send",
                headers=HEADERS,
                json={"message": _text_message(taskId="missing-task")},
            )
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "task_not_found"

            terminal = await client.post(
                "/a2a/message:send",
                headers=HEADERS,
                json={"message": _text_message(taskId=handle.invocation_id)},
            )
            assert terminal.status_code == 409
            assert terminal.json()["error"]["code"] == "task_not_accepting_input"

            started = await client.post(
                "/a2a/message:send",
                headers=HEADERS,
                json={
                    "message": _text_message(),
                    "configuration": {"returnImmediately": True},
                },
            )
            assert started.status_code == 200
            assert "task" in started.json()
            task_id = started.json()["task"]["id"]
            await _poll_task_state(client, task_id, "TASK_STATE_COMPLETED", transport="http_json")
