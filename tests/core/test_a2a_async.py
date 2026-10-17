"""Protocol-native async behavior for the bounded inbound A2A profile.

Covers ``SendMessageConfiguration.returnImmediately``, Task-returning sends,
and resuming sends over the common resume contract (A2A-6).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

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
    message: dict[str, object] = {
        "messageId": "fixture-message",
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }
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
@pytest.mark.parametrize("transport", ["jsonrpc", "http_json"])
@pytest.mark.parametrize(
    ("suppress_cancellation", "late_outcome"),
    [(False, "value"), (True, "value"), (True, "fault")],
)
async def test_host_drain_keeps_inflight_task_authoritative_in_later_reads(
    tmp_path, transport: str, suppress_cancellation: bool, late_outcome: str
) -> None:
    entered = asyncio.Event()
    interrupted = asyncio.Event()
    suppressed = asyncio.Event()
    release = asyncio.Event()

    async def slow_model(_prompt):
        def late_result():
            if late_outcome == "fault":
                raise RuntimeError("late provider failure")
            return {"response": "late-result"}

        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            if suppress_cancellation:
                suppressed.set()
                await asyncio.shield(release.wait())
                return late_result()
            interrupted.set()
            raise
        return late_result()

    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "workflow": {
                "catalog": [
                    {
                        "document": {
                            "dsl": "1.0.3",
                            "namespace": "a2a-host-drain",
                            "name": "slow",
                            "version": "1.0.0",
                        },
                        "do": [
                            {
                                "wait": {
                                    "call": "llm:1.0.0@default",
                                    "with": {"prompt": "wait for host drain"},
                                }
                            }
                        ],
                    }
                ]
            },
            "a2a": {
                "enabled": True,
                "transport": transport,
                "skills": [{"id": "slow", "workflow": "slow"}],
            },
        }
    )
    services = RuntimeServices(config, model=FakeModel(slow_model), database_root=tmp_path)
    app = create_app(config=config, services=services)
    try:
        async with app.router.lifespan_context(app):
            client_transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=client_transport, base_url="http://test"
            ) as client:
                if transport == "jsonrpc":
                    started = await client.post(
                        "/a2a",
                        headers=HEADERS,
                        json=_send(
                            _text_message("start the slow task", metadata={"skillId": "slow"}),
                            configuration={"returnImmediately": True},
                        ),
                    )
                    assert started.status_code == 200, started.text
                    task_id = started.json()["result"]["task"]["id"]
                else:
                    started = await client.post(
                        "/a2a/message:send",
                        headers=HEADERS,
                        json={
                            "message": _text_message(
                                "start the slow task", metadata={"skillId": "slow"}
                            ),
                            "configuration": {"returnImmediately": True},
                        },
                    )
                    assert started.status_code == 200, started.text
                    task_id = started.json()["task"]["id"]

                await asyncio.wait_for(entered.wait(), timeout=30)
                before_drain = services.invocations.get(task_id)
                assert before_drain is not None and before_drain.status == "running"

                shutdown = asyncio.create_task(app.state.engine.shutdown())
                if suppress_cancellation:
                    await asyncio.wait_for(suppressed.wait(), timeout=5)
                    assert not shutdown.done()
                    release.set()
                else:
                    await asyncio.wait_for(interrupted.wait(), timeout=5)
                await asyncio.wait_for(shutdown, timeout=5)
                after_drain = services.invocations.get(task_id)
                assert after_drain is not None and after_drain.status == "running"
                persisted = after_drain._persisted_payload
                model_calls_after_drain = list(services.model.calls)

                first = await _poll_task_state(
                    client, task_id, "TASK_STATE_WORKING", transport=transport
                )
                second = await _poll_task_state(
                    client, task_id, "TASK_STATE_WORKING", transport=transport
                )
                assert second == first
                assert services.invocations.get(task_id)._persisted_payload == persisted
                assert services.model.calls == model_calls_after_drain
    finally:
        release.set()
        services.close()


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
@pytest.mark.parametrize("transport_name", ["jsonrpc", "http_json"])
async def test_a2a_resume_cannot_bypass_protected_approval_wait(
    tmp_path, monkeypatch, transport_name
) -> None:
    monkeypatch.setenv("OWA_TEST_A2A_APPROVAL_ACTOR", "approval-secret")
    subject = f"protected-a2a-{transport_name}"
    expires_at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "a2a-async",
            "name": "protected-approval-flow",
            "version": "1.0.0",
        },
        "do": [
            {
                "request": {
                    "emit": {
                        "event": {
                            "with": {
                                "id": subject,
                                "source": "urn:a2a-async:protected-approval",
                                "type": APPROVAL_REQUEST_EVENT,
                                "subject": subject,
                                "approvalexpiresat": expires_at,
                                "data": {
                                    "approvalPolicy": {
                                        "profile": "owa/protected-approval/v1",
                                        "assignee": "reviewer-a",
                                        "reassignmentTargets": [],
                                        "escalationTargets": [],
                                    },
                                    "question": "continue?",
                                },
                            }
                        }
                    }
                }
            },
            {
                "decision": {
                    "listen": {
                        "to": {
                            "one": {
                                "with": {
                                    "type": APPROVAL_DECISION_EVENT,
                                    "subject": subject,
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
    config = RuntimeConfig.model_validate(
        {
            "approvals": {
                "enabled": True,
                "protected": True,
                "actor_security_profiles": ["approval-actor"],
            },
            "security": {
                "profiles": {
                    "approval-actor": {
                        "type": "bearer",
                        "token": {"from_env": "OWA_TEST_A2A_APPROVAL_ACTOR"},
                        "principal": "reviewer-a",
                    }
                }
            },
            "workflow": {"catalog": [workflow]},
            "a2a": {
                "enabled": True,
                "transport": transport_name,
                "skills": [{"id": "approve", "workflow": "protected-approval-flow"}],
            },
        }
    )
    app, services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            if transport_name == "jsonrpc":
                started = await client.post(
                    "/a2a",
                    headers=HEADERS,
                    json=_send(
                        _text_message(metadata={"skillId": "approve"}),
                        configuration={"returnImmediately": True},
                    ),
                )
            else:
                started = await client.post(
                    "/a2a/message:send",
                    headers=HEADERS,
                    json={
                        "message": _text_message(metadata={"skillId": "approve"}),
                        "configuration": {"returnImmediately": True},
                    },
                )
            assert started.status_code == 200, started.text
            started_body = started.json()
            task_id = (
                started_body["result"]["task"]["id"]
                if transport_name == "jsonrpc"
                else started_body["task"]["id"]
            )
            await _poll_task_state(
                client, task_id, "TASK_STATE_INPUT_REQUIRED", transport=transport_name
            )

            handle = services.invocations.get(task_id)
            assert handle is not None and handle.status == "waiting"
            input_before = services.invocations.read_input(handle)
            payload_before = handle._persisted_payload
            delivery_before = services.initial_deliveries.get(handle)
            assert delivery_before is not None
            approval_before = services.approvals.protected_store.inspect(subject)
            request_before = services.approvals.store.get(subject)
            assert request_before is not None and request_before.status == "pending"
            inputs_before = services.invocations.connection.execute(
                "SELECT invocation_id,fingerprint,payload FROM invocation_inputs "
                "WHERE invocation_id=?",
                (task_id,),
            ).fetchall()
            effects_before = services.effect_journal.connection.execute(
                "SELECT i.identity,i.fingerprint,i.payload,e.owner,e.status,e.payload "
                "FROM workflow_effect_invocations i LEFT JOIN workflow_effects e "
                "ON e.identity=i.identity WHERE i.invocation_id=? ORDER BY i.identity",
                (task_id,),
            ).fetchall()
            inbox_before = services.event_store.connection.execute(
                "SELECT sequence,scope,source,event_id,envelope,digest,submitted_digest "
                "FROM event_inbox ORDER BY sequence"
            ).fetchall()

            resume_message = _text_message(
                "unauthorized approval answer",
                taskId=task_id,
                contextId=handle.session_id,
            )
            if transport_name == "jsonrpc":
                resumed = await client.post(
                    "/a2a",
                    headers=HEADERS,
                    json=_send(resume_message),
                )
            else:
                resumed = await client.post(
                    "/a2a/message:send",
                    headers=HEADERS,
                    json={"message": resume_message},
                )
            if transport_name == "jsonrpc":
                assert resumed.status_code == 200, resumed.text
                assert resumed.json()["error"]["code"] == -32000
                assert resumed.json()["error"]["message"] == "task is not accepting input"
            else:
                assert resumed.status_code == 409, resumed.text
                assert resumed.json()["error"]["details"][0]["metadata"]["owaCode"] == (
                    "task_not_resumeable"
                )

            current = services.invocations.get(task_id)
            assert current is not None and current.status == "waiting"
            assert current._persisted_payload == payload_before
            assert services.invocations.read_input(current) == input_before
            assert services.initial_deliveries.get(current) == delivery_before
            assert services.approvals.protected_store.inspect(subject) == approval_before
            assert services.approvals.store.get(subject) == request_before
            assert services.approvals.store.get(subject).status == "pending"
            assert (
                services.invocations.connection.execute(
                    "SELECT invocation_id,fingerprint,payload FROM invocation_inputs "
                    "WHERE invocation_id=?",
                    (task_id,),
                ).fetchall()
                == inputs_before
            )
            assert (
                services.effect_journal.connection.execute(
                    "SELECT i.identity,i.fingerprint,i.payload,e.owner,e.status,e.payload "
                    "FROM workflow_effect_invocations i LEFT JOIN workflow_effects e "
                    "ON e.identity=i.identity WHERE i.invocation_id=? ORDER BY i.identity",
                    (task_id,),
                ).fetchall()
                == effects_before
            )
            assert (
                services.event_store.connection.execute(
                    "SELECT sequence,scope,source,event_id,envelope,digest,submitted_digest "
                    "FROM event_inbox ORDER BY sequence"
                ).fetchall()
                == inbox_before
            )
            assert services.model.calls == []


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
            assert error["code"] == -32004
            assert error["message"] == "task is not accepting input"


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_name", ["jsonrpc", "http_json"])
async def test_resuming_send_restarts_persisted_waiting_task(tmp_path, transport_name) -> None:
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
                        "do": [
                            {"pause": {"wait": {"seconds": 300}}},
                            {"mark": {"set": {"resumed": True}}},
                        ],
                    }
                ]
            },
            "a2a": {
                "enabled": True,
                "transport": transport_name,
                "skills": [{"id": "echo", "workflow": "echo-flow"}],
            },
        }
    )
    app, services = _make_app(tmp_path, config)
    async with app.router.lifespan_context(app):
        plan = app.state.a2a_skill_plans["echo"]
        handle = services.invocations.create(
            engine=app.state.engine.engine_name,
            session_id="original-context",
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        original_input = {"original": "first message"}
        waiting = await asyncio.wait_for(
            app.state.engine.invoke(plan, handle, original_input), timeout=10
        )
        assert waiting.status == "waiting", waiting.error
        assert handle.status == "waiting"
        assert handle.invocation_id not in app.state.engine._active
        assert handle._execution_lease is None
        assert services.invocations.read_input(handle) == original_input
        assert not any(
            event.invocation_id == handle.invocation_id and event.task_name == "mark"
            for event in services.events.events
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            message = _text_message(
                "continue",
                taskId=handle.invocation_id,
                contextId=handle.session_id,
            )
            if transport_name == "jsonrpc":
                response = await client.post(
                    "/a2a",
                    headers=HEADERS,
                    json=_send(message),
                )
                body = response.json()["result"]
            else:
                response = await client.post(
                    "/a2a/message:send",
                    headers=HEADERS,
                    json={"message": message},
                )
                body = response.json()
            assert response.status_code == 200
            assert body["task"]["id"] == handle.invocation_id
            assert body["task"]["contextId"] == "original-context"
            assert body["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
            assert body["task"]["artifacts"][0]["parts"][0]["data"] == {"resumed": True}

            final = await _poll_task_state(
                client,
                handle.invocation_id,
                "TASK_STATE_COMPLETED",
                transport=transport_name,
            )
            assert final["id"] == handle.invocation_id
            assert final["contextId"] == "original-context"
            assert final["artifacts"][0]["parts"][0]["data"] == {"resumed": True}
            assert services.invocations.read_input(handle) == original_input
            events = [
                event
                for event in services.events.events
                if event.invocation_id == handle.invocation_id
            ]
            assert sum(event.event_type == "WorkflowResumed" for event in events) == 1
            assert (
                sum(
                    event.event_type == "TaskStarted" and event.task_name == "mark"
                    for event in events
                )
                == 1
            )
            assert (
                sum(
                    event.event_type == "TaskCompleted" and event.task_name == "mark"
                    for event in events
                )
                == 1
            )
            assert services.model.calls == []


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
            assert missing.json()["error"]["details"][0]["metadata"]["owaCode"] == "task_not_found"

            terminal = await client.post(
                "/a2a/message:send",
                headers=HEADERS,
                json={"message": _text_message(taskId=handle.invocation_id)},
            )
            assert terminal.status_code == 400
            assert (
                terminal.json()["error"]["details"][0]["metadata"]["owaCode"]
                == "task_not_accepting_input"
            )

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
