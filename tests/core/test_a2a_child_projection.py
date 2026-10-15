"""A2A GetTask preserves terminal outcomes from a real nested child wait."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import WorkflowExecutor


def _message() -> dict[str, Any]:
    return {
        "messageId": "child-projection-request",
        "role": "ROLE_USER",
        "parts": [{"text": "start the parent workflow"}],
        "metadata": {"skillId": "parent"},
    }


def _request(binding: str, method: str, request_id: str, *, task_id: str | None = None):
    if method == "SendMessage":
        params = {"message": _message(), "configuration": {"returnImmediately": True}}
    else:
        assert task_id is not None
        params = {"id": task_id}
    if binding == "jsonrpc":
        return "/a2a", {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
    if method == "SendMessage":
        return "/a2a/message:send", params
    return f"/a2a/tasks/{task_id}", params


async def _get_task(client: httpx.AsyncClient, binding: str, task_id: str, request_id: str):
    if binding == "http_json":
        return await client.get(
            f"/a2a/tasks/{task_id}", headers={"A2A-Version": A2A_PROTOCOL_VERSION}
        )
    return await client.post(
        "/a2a",
        headers={"A2A-Version": A2A_PROTOCOL_VERSION},
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "GetTask",
            "params": {"id": task_id},
        },
    )


def _task(response: httpx.Response, binding: str):
    payload = response.json()
    return payload["result"] if binding == "jsonrpc" else payload


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["jsonrpc"])
async def test_get_task_projects_committed_terminal_nested_child_outcome_immutably(
    tmp_path, monkeypatch, binding: str
) -> None:
    current = [1000.0]
    original_initialize = WorkflowExecutor.__init__

    def initialize(executor: WorkflowExecutor, *args: Any, **kwargs: Any) -> None:
        original_initialize(executor, *args, **kwargs)
        executor._absolute_clock = lambda: current[0]

    monkeypatch.setattr(WorkflowExecutor, "__init__", initialize)
    child_tail = {"finish": {"set": {"child_result": "actual"}}}
    child = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "a2a-child-projection",
            "name": "child",
            "version": "1.0.0",
        },
        "do": [{"pause": {"wait": {"seconds": 60}}}, child_tail],
    }
    parent = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "a2a-child-projection",
            "name": "parent",
            "version": "1.0.0",
        },
        "do": [
            {
                "start": {
                    "run": {
                        "workflow": {
                            "namespace": "a2a-child-projection",
                            "name": "child",
                            "version": "1.0.0",
                        }
                    }
                }
            }
        ],
    }
    config = RuntimeConfig.model_validate(
        {
            "workflow": {"catalog": [child, parent]},
            "a2a": {
                "enabled": True,
                "transport": binding,
                "skills": [{"id": "parent", "workflow": "parent"}],
            },
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    app = create_app(config=config, services=services, engine=PortableWorkflowEngine())
    try:
        async with app.router.lifespan_context(app):
            dispatcher = app.state.engine._timer_dispatcher
            dispatcher.clock = lambda: current[0]
            dispatcher.poll_seconds = 3600
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                path, payload = _request(binding, "SendMessage", "start")
                started = await client.post(
                    path,
                    headers={"A2A-Version": A2A_PROTOCOL_VERSION},
                    json=payload,
                )
                assert started.status_code == 200, started.text
                start_result = started.json()
                start_body = start_result["result"] if binding == "jsonrpc" else start_result
                task_id = start_body["task"]["id"]

                async with asyncio.timeout(5):
                    while True:
                        handle = services.invocations.get(task_id)
                        if handle is not None and handle.status in {
                            "completed",
                            "faulted",
                            "cancelled",
                        }:
                            pytest.fail(
                                f"parent reached {handle.status} before child wait: {handle.error}"
                            )
                        waits = (
                            services.child_waits.for_invocation(
                                task_id, handle.workflow_fingerprint
                            )
                            if handle is not None
                            else []
                        )
                        if handle is not None and handle.status == "waiting" and waits:
                            break
                        await asyncio.sleep(0.01)
                registration = waits[0]
                child_id = registration.suspension.child_id
                child_handle = services.invocations.get(child_id)
                assert child_handle is not None and child_handle.status == "waiting"

                waiting = await _get_task(client, binding, task_id, "waiting")
                assert waiting.status_code == 200, waiting.text
                waiting_task = _task(waiting, binding)
                assert waiting_task["id"] == task_id
                assert waiting_task["status"]["state"] == "TASK_STATE_WORKING"
                timer = services.timers.pending_for_invocation(
                    child_id, child_handle.workflow_fingerprint
                )[0]
                assert timer.due_at == 1060.0

                current[0] = 1060.0
                assert await dispatcher.tick(now=current[0], invocation_id=child_id) == 1
                async with asyncio.timeout(5):
                    while child_id in dispatcher._workers:
                        await asyncio.sleep(0.01)
                assert services.invocations.get(child_id).status == "completed"

                await dispatcher.child_waits.tick(run_key=registration.identity.key)
                async with asyncio.timeout(5):
                    while task_id in dispatcher.child_waits._workers:
                        await asyncio.sleep(0.01)
                committed = services.invocations.get(task_id)
                assert committed is not None
                assert committed.status == "completed"
                assert committed.output == {"child_result": "actual"}

                terminal = await _get_task(client, binding, task_id, "terminal")
                assert terminal.status_code == 200, terminal.text
                terminal_task = _task(terminal, binding)
                assert terminal_task["id"] == task_id
                assert terminal_task["contextId"] == waiting_task["contextId"]
                assert terminal_task["status"]["state"] == "TASK_STATE_COMPLETED"
                assert terminal_task["artifacts"][0]["parts"][0]["data"] == {
                    "child_result": "actual"
                }

                persisted = committed._persisted_payload
                repeated = await _get_task(client, binding, task_id, "repeated")
                assert repeated.status_code == 200, repeated.text
                assert _task(repeated, binding) == terminal_task
                assert services.invocations.get(task_id)._persisted_payload == persisted
    finally:
        services.close()
