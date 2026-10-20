"""Public resume retains a root definition while a new deployment admits v2."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.a2a import A2A_PROTOCOL_VERSION
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError, WorkflowDefinitionChanged
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from test_native_checkpoint_fencing import _snapshot as _native_snapshot
from test_synchronous_child_waits import _advance_child, _advance_parent, _clock

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")
PRINCIPAL = "version-owner"
TOKEN_ENV = "OWA_VERSION_TEST_TOKEN"
TOKEN = "deterministic-version-test-token"
ENGINES = [case for case in engine_cases() if case[0] in {"adk", "langgraph"}]


def _definition(version: str) -> dict[str, Any]:
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "version-recovery",
            "name": "retained-root",
            "version": version,
        },
        "timeout": {"after": {"seconds": 300}},
        "do": [
            {
                "commit": {
                    "call": "http",
                    "with": {
                        "method": "POST",
                        "endpoint": "https://receiver.test/commit",
                        "body": {"version": version, "original": "${ $workflow.input }"},
                    },
                }
            },
            *([{"pause": {"wait": {"seconds": 600}}}] if version == "1.0.0" else []),
            {
                "finish": {
                    "set": {
                        "version": version,
                        "original": "${ $workflow.input }",
                        **({"resume": "${ $resume_input }"} if version == "1.0.0" else {}),
                    }
                }
            },
        ],
    }


def _config(
    source: dict[str, Any],
    backend: str,
    *,
    skill: bool = False,
    a2a_transport: str = "jsonrpc",
) -> RuntimeConfig:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    return RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "workflow": {"definition": source},
            "server": {"api_security_profile": "api"},
            "a2a": {
                "enabled": True,
                "security_profile": "api",
                "transport": a2a_transport,
                **(
                    {
                        "skills": [{"id": "retained", "workflow": "retained-root"}],
                        "authorization": {
                            "rules": [
                                {
                                    "actions": ["message.send"],
                                    "resources": ["skill:retained"],
                                    "roles": ["version-users"],
                                }
                            ]
                        },
                    }
                    if skill
                    else {}
                ),
            },
            "security": {
                "profiles": {
                    "api": {
                        "type": "bearer",
                        "principal": PRINCIPAL,
                        "roles": ["version-users"],
                        "token": {"from_env": TOKEN_ENV},
                    }
                }
            },
        }
    )


class Receiver:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[tuple[str, dict[str, Any]]] = []
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS commits(operation_id TEXT PRIMARY KEY,body TEXT)"
            )
            connection.commit()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        key, body = request.headers["Idempotency-Key"], json.loads(request.content)
        self.calls.append((key, body))
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO commits VALUES (?,?) ON CONFLICT(operation_id) DO NOTHING",
                    (key, json.dumps(body, sort_keys=True)),
                )
                original = json.loads(
                    connection.execute(
                        "SELECT body FROM commits WHERE operation_id=?", (key,)
                    ).fetchone()[0]
                )
                assert original == body
        return httpx.Response(200, json={"accepted": body})

    def rows(self) -> list[Any]:
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute("SELECT * FROM commits ORDER BY operation_id").fetchall()


def _services(path: Path, config: RuntimeConfig, receiver: Receiver) -> RuntimeServices:
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if config.persistence.datasource else path
    )
    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(receiver)))
    services.tools.protocols = services.protocols
    return services


def _common_snapshot(services: RuntimeServices, invocation_id: str) -> dict[str, Any]:
    return {
        table: services.invocations.connection.execute(statement, (invocation_id,)).fetchall()
        for table, statement in {
            "invocation": "SELECT * FROM invocations WHERE invocation_id=?",
            "input": "SELECT * FROM invocation_inputs WHERE invocation_id=?",
            "binding": "SELECT * FROM recovery_invocation_definitions WHERE invocation_id=?",
            "ownership": "SELECT * FROM invocation_ownership WHERE invocation_id=?",
            "authority": "SELECT * FROM execution_authorities WHERE invocation_id=?",
            "authority_candidate": "SELECT * FROM execution_authority_candidates "
            "WHERE candidate_key='invocation:' || ?",
            "effects": "SELECT i.identity,e.payload FROM workflow_effect_invocations i "
            "JOIN workflow_effects e ON e.identity=i.identity WHERE i.invocation_id=? "
            "ORDER BY i.identity",
        }.items()
    }


def _actual_native(engine: Any, engine_name: str) -> None:
    available = (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    )
    if not available:
        pytest.skip("actual independent native dependency environment is required")


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("original", [{"secret": "private-original-input"}, None])
@pytest.mark.parametrize("delivery", ["rest", "a2a", "a2a_skill"])
async def test_public_resume_uses_retained_v1_after_v2_deployment_and_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    original: Any,
    delivery: str,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    _clock(monkeypatch, [1000.0])
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    first_config = _config(_definition("1.0.0"), backend, skill=delivery == "a2a_skill")
    services = _services(tmp_path, first_config, receiver)
    first = engine_type()
    _actual_native(first, engine_name)
    app = create_app(config=first_config, services=services, engine=first)
    headers = {"Authorization": "Bearer " + TOKEN}
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
            ) as client:
                started = await client.post(
                    "/v1/invoke", json={"user_id": "correlation", "input": original}
                )
                assert started.status_code == 200 and started.json()["status"] == "waiting"
                invocation_id = started.json()["invocation_id"]
                saved = services.invocations.get(invocation_id)
                assert saved is not None and saved.workflow_version == "1.0.0"
                saved_identity = (
                    saved.invocation_id,
                    saved.session_id,
                    saved.user_id,
                    saved.owner_principal,
                    saved.workflow_fingerprint,
                    saved.engine_execution_reference,
                )
                original_receipts = _common_snapshot(services, invocation_id)["effects"]
                original_due = services.timers.connection.execute(
                    "SELECT identity,due_at,wake_at FROM workflow_timers WHERE invocation_id=?",
                    (invocation_id,),
                ).fetchall()
                assert len(original_due) == 1 and original_due[0][1:] == (1600.0, 1300.0)
                before = _common_snapshot(services, invocation_id)
                native_before = _native_snapshot(services, saved, backend)
                inspected = await client.get(f"/v1/invocations/{invocation_id}/recovery")
                assert inspected.status_code == 200
                report = inspected.json()
                assert report["definition"]["version"] == "1.0.0"
                assert report["native"] == "not_inspected"
                assert report["actions"] == {"known": 1, "pending": 0, "complete": True}
                assert (
                    "private-original-input" not in inspected.text and TOKEN not in inspected.text
                )
                assert _common_snapshot(services, invocation_id) == before
                assert _native_snapshot(services, saved, backend) == native_before
                assert len(receiver.calls) == 1 and not services.model.calls
        services.close()

        next_config = _config(_definition("2.0.0"), backend, skill=delivery == "a2a_skill")
        services = _services(tmp_path, next_config, receiver)
        second = engine_type()
        app = create_app(config=next_config, services=services, engine=second)
        async with app.router.lifespan_context(app):
            assert app.state.plan.version == "2.0.0"
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
            ) as client:
                if delivery == "rest":
                    resumed = await client.post(
                        f"/v1/invocations/{invocation_id}/resume", json={"input": None}
                    )
                    output = resumed.json().get("output")
                    expected_resume: Any = None
                else:
                    resumed = await client.post(
                        "/a2a",
                        headers={"a2a-version": A2A_PROTOCOL_VERSION},
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "SendMessage",
                            "params": {
                                "message": {
                                    "role": "ROLE_USER",
                                    "messageId": "resume-original-version",
                                    "taskId": invocation_id,
                                    "parts": [{"text": "continue original"}],
                                }
                            },
                        },
                    )
                    projected = resumed.json()["result"]["task"]
                    assert projected["id"] == invocation_id
                    assert projected["contextId"] == saved_identity[1]
                    assert projected["status"]["state"] == "TASK_STATE_COMPLETED"
                    output = projected["artifacts"][0]["parts"][0]["data"]
                    expected_resume = {"question": "continue original"}
                assert resumed.status_code == 200, resumed.text
                assert output == {
                    "version": "1.0.0",
                    "original": original,
                    "resume": expected_resume,
                }
                saved = services.invocations.get(invocation_id)
                assert saved is not None
                assert (
                    saved.invocation_id,
                    saved.session_id,
                    saved.user_id,
                    saved.owner_principal,
                    saved.workflow_fingerprint,
                    saved.engine_execution_reference,
                ) == saved_identity
                assert services.invocations.read_input(saved) == original
                recovered_receipts = dict(_common_snapshot(services, invocation_id)["effects"])
                assert all(recovered_receipts[key] == payload for key, payload in original_receipts)
                assert (
                    services.timers.connection.execute(
                        "SELECT identity,due_at,wake_at FROM workflow_timers WHERE invocation_id=?",
                        (invocation_id,),
                    ).fetchall()
                    == original_due
                )
                duplicate = await client.post(
                    f"/v1/invocations/{invocation_id}/resume", json={"input": {"replacement": True}}
                )
                assert duplicate.status_code == 200 and duplicate.json()["status"] == "completed"
                assert duplicate.json()["output"] == output
                fresh = await client.post("/v1/invoke", json={"input": {"new": True}})
                assert fresh.status_code == 200 and fresh.json()["status"] == "completed"
                assert fresh.json()["output"] == {"version": "2.0.0", "original": {"new": True}}
                assert len(receiver.calls) == len(receiver.rows()) == 2
                assert [body["version"] for _, body in receiver.calls] == ["1.0.0", "2.0.0"]
                assert len({key for key, _ in receiver.calls}) == 2 and not services.model.calls
    finally:
        await app.state.engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("surface", ["jsonrpc", "http_json"])
async def test_a2a_skill_resume_keeps_archived_workflow_after_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    surface: str,
) -> None:
    """A declared current skill may resume its existing task on the archived version."""
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    _clock(monkeypatch, [1000.0])
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    headers = {"Authorization": "Bearer " + TOKEN, "a2a-version": A2A_PROTOCOL_VERSION}
    first_config = _config(_definition("1.0.0"), backend, skill=True, a2a_transport=surface)
    services = _services(tmp_path, first_config, receiver)
    engine = engine_type()
    _actual_native(engine, engine_name)
    app = create_app(config=first_config, services=services, engine=engine)
    invocation_id = ""
    session_id = ""
    original_input = {"question": "start through retained skill"}
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=headers,
            ) as client:
                message = {
                    "role": "ROLE_USER",
                    "messageId": "start-retained-skill",
                    "parts": [{"text": original_input["question"]}],
                    "metadata": {"skillId": "retained"},
                }
                if surface == "jsonrpc":
                    started = await client.post(
                        "/a2a",
                        json={
                            "jsonrpc": "2.0",
                            "id": "start-retained-skill",
                            "method": "SendMessage",
                            "params": {
                                "message": message,
                                "configuration": {"returnImmediately": True},
                            },
                        },
                    )
                    assert started.status_code == 200, started.text
                    task = started.json()["result"]["task"]
                else:
                    started = await client.post(
                        "/a2a/message:send",
                        json={
                            "message": message,
                            "configuration": {"returnImmediately": True},
                        },
                    )
                    assert started.status_code == 200, started.text
                    task = started.json()["task"]
                invocation_id = task["id"]
                session_id = task["contextId"]
                handle = services.invocations.get(invocation_id)
                assert handle is not None and handle.status in {"running", "waiting"}
                assert handle.workflow_version == "1.0.0"
                assert handle.owner_principal == PRINCIPAL
                assert services.invocations.read_input(handle) == original_input

                async def wait_until_waiting() -> None:
                    while True:
                        current = services.invocations.get(invocation_id)
                        assert current is not None
                        if current.status == "waiting":
                            return
                        assert current.status == "running"
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(wait_until_waiting(), timeout=30)
                waiting = services.invocations.get(invocation_id)
                assert waiting is not None and waiting.session_id == session_id
                assert waiting.owner_principal == PRINCIPAL
                assert len(receiver.calls) == len(receiver.rows()) == 1
                assert not services.model.calls
        services.close()

        next_config = _config(_definition("2.0.0"), backend, skill=True, a2a_transport=surface)
        services = _services(tmp_path, next_config, receiver)
        engine = engine_type()
        app = create_app(config=next_config, services=services, engine=engine)
        async with app.router.lifespan_context(app):
            assert app.state.plan.version == "2.0.0"
            assert app.state.a2a_skill_plans["retained"].version == "2.0.0"
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=headers,
            ) as client:
                message = {
                    "role": "ROLE_USER",
                    "messageId": "resume-retained-skill",
                    "taskId": invocation_id,
                    "contextId": session_id,
                    "parts": [{"text": "continue through retained skill"}],
                }
                if surface == "jsonrpc":
                    resumed = await client.post(
                        "/a2a",
                        json={
                            "jsonrpc": "2.0",
                            "id": "resume-retained-skill",
                            "method": "SendMessage",
                            "params": {"message": message},
                        },
                    )
                    assert resumed.status_code == 200, resumed.text
                    task = resumed.json()["result"]["task"]
                else:
                    resumed = await client.post("/a2a/message:send", json={"message": message})
                    assert resumed.status_code == 200, resumed.text
                    task = resumed.json()["task"]
                assert task["id"] == invocation_id
                assert task["contextId"] == session_id
                assert task["status"]["state"] == "TASK_STATE_COMPLETED"
                assert task["artifacts"][0]["parts"][0]["data"] == {
                    "version": "1.0.0",
                    "original": original_input,
                    "resume": {"question": "continue through retained skill"},
                }
                recovered = services.invocations.get(invocation_id)
                assert recovered is not None
                assert recovered.workflow_version == "1.0.0"
                assert recovered.session_id == session_id
                assert recovered.owner_principal == PRINCIPAL
                assert services.invocations.read_input(recovered) == original_input
                assert len(receiver.calls) == len(receiver.rows()) == 1
                assert [body["version"] for _, body in receiver.calls] == ["1.0.0"]
                assert not services.model.calls
    finally:
        await app.state.engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "damage", ["archive_missing", "archive_digest", "binding_missing", "binding_digest"]
)
@pytest.mark.parametrize("surface", ["rest", "jsonrpc", "http_json"])
async def test_public_resume_rejects_changed_archive_before_ownership_or_native_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    damage: str,
    surface: str,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    _clock(monkeypatch, [1000.0])
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    config = _config(
        _definition("1.0.0"),
        backend,
        a2a_transport=surface if surface in {"jsonrpc", "http_json"} else "jsonrpc",
    )
    services = _services(tmp_path, config, receiver)
    engine = engine_type()
    _actual_native(engine, engine_name)
    app = create_app(config=config, services=services, engine=engine)
    headers = {"Authorization": "Bearer " + TOKEN}
    restore: tuple[str, tuple[str, ...], Any] | None = None
    handle: Any = None
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
            ) as client:
                started = await client.post(
                    "/v1/invoke", json={"input": {"secret": "retained-secret"}}
                )
                assert started.status_code == 200 and started.json()["status"] == "waiting"
                handle = services.invocations.get(started.json()["invocation_id"])
                assert handle is not None
                connection = services.recovery_definitions.connection
                if damage.startswith("archive"):
                    table, column, key = (
                        "recovery_definitions",
                        "fingerprint",
                        handle.workflow_fingerprint,
                    )
                else:
                    table, column, key = (
                        "recovery_invocation_definitions",
                        "invocation_id",
                        handle.invocation_id,
                    )
                columns = (
                    (column, "payload", "digest")
                    if table == "recovery_definitions"
                    else (column, "fingerprint", "payload", "digest")
                )
                original_row = connection.execute(
                    f"SELECT {','.join(columns)} FROM {table} WHERE {column}=?", (key,)
                ).fetchone()
                assert original_row is not None
                restore = (table, columns, original_row)
                statement = (
                    f"DELETE FROM {table} WHERE {column}=?"
                    if damage.endswith("missing")
                    else f"UPDATE {table} SET digest='invalid' WHERE {column}=?"
                )
                assert connection.execute(statement, (key,)).rowcount == 1
                connection.commit()
                before = _common_snapshot(services, handle.invocation_id)
                native_before = _native_snapshot(services, handle, backend)
                if surface == "rest":
                    response = await client.post(
                        f"/v1/invocations/{handle.invocation_id}/resume",
                        json={"input": {"replacement": True}},
                    )
                    assert response.status_code in {409, 500}, response.text
                else:
                    message = {
                        "role": "ROLE_USER",
                        "messageId": "reject-damaged-archive",
                        "taskId": handle.invocation_id,
                        **(
                            {"contextId": handle.session_id}
                            if handle.session_id is not None
                            else {}
                        ),
                        "parts": [{"text": "continue damaged archive"}],
                    }
                    if surface == "jsonrpc":
                        response = await client.post(
                            "/a2a",
                            headers={"a2a-version": A2A_PROTOCOL_VERSION},
                            json={
                                "jsonrpc": "2.0",
                                "id": "damaged-archive-resume",
                                "method": "SendMessage",
                                "params": {"message": message},
                            },
                        )
                        body = response.json()
                        assert response.status_code == 200, response.text
                        assert body["error"]["code"] == -32000
                        assert body["error"]["message"] == "task is not accepting input"
                    else:
                        response = await client.post(
                            "/a2a/message:send",
                            headers={"a2a-version": A2A_PROTOCOL_VERSION},
                            json={"message": message},
                        )
                        body = response.json()
                        assert response.status_code == 409, response.text
                        assert body["error"]["details"][0]["metadata"]["owaCode"] == (
                            "task_not_resumeable"
                        )
                assert "retained-secret" not in response.text and TOKEN not in response.text
                report = await client.get(f"/v1/invocations/{handle.invocation_id}/recovery")
                assert report.status_code == 200 and report.json()["continuation"] == "unavailable"
                assert report.json()["checks"][0]["stage"] == "definition"
                assert "retained-secret" not in report.text and TOKEN not in report.text
                assert _common_snapshot(services, handle.invocation_id) == before
                assert _native_snapshot(services, handle, backend) == native_before
                assert len(receiver.calls) == len(receiver.rows()) == 1 and not services.model.calls
    finally:
        if restore is not None:
            table, columns, row = restore
            column = columns[0]
            connection = services.recovery_definitions.connection
            connection.execute(
                f"INSERT INTO {table}({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)}) "
                f"ON CONFLICT({column}) DO UPDATE SET "
                "payload=excluded.payload,digest=excluded.digest",
                tuple(row),
            )
            connection.commit()
        if handle is not None:
            await engine.cancel(handle, operation_id="version-fixture-cleanup")
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("surface", ["jsonrpc", "http_json"])
@pytest.mark.parametrize("damage", ["missing", "fingerprint"])
async def test_a2a_resume_rejects_changed_owner_authority_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    surface: str,
    damage: str,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    _clock(monkeypatch, [1000.0])
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    config = _config(_definition("1.0.0"), backend, a2a_transport=surface)
    services = _services(tmp_path, config, receiver)
    engine = engine_type()
    _actual_native(engine, engine_name)
    app = create_app(config=config, services=services, engine=engine)
    headers = {"Authorization": "Bearer " + TOKEN}
    handle: Any = None
    original_authority: Any = None
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
            ) as client:
                started = await client.post(
                    "/v1/invoke", json={"input": {"secret": "owner-evidence-secret"}}
                )
                assert started.status_code == 200 and started.json()["status"] == "waiting"
                handle = services.invocations.get(started.json()["invocation_id"])
                assert handle is not None and handle.owner_principal == PRINCIPAL
                connection = services.execution_authorizations.connection
                original_authority = connection.execute(
                    "SELECT invocation_id,fingerprint,payload,digest FROM execution_authorities "
                    "WHERE invocation_id=?",
                    (handle.invocation_id,),
                ).fetchone()
                assert original_authority is not None
                if damage == "missing":
                    connection.execute(
                        "DELETE FROM execution_authorities WHERE invocation_id=?",
                        (handle.invocation_id,),
                    )
                else:
                    connection.execute(
                        "UPDATE execution_authorities SET fingerprint='invalid' "
                        "WHERE invocation_id=?",
                        (handle.invocation_id,),
                    )
                connection.commit()

                before = _common_snapshot(services, handle.invocation_id)
                native_before = _native_snapshot(services, handle, backend)
                message = {
                    "role": "ROLE_USER",
                    "messageId": "reject-changed-owner-authority",
                    "taskId": handle.invocation_id,
                    **({"contextId": handle.session_id} if handle.session_id is not None else {}),
                    "parts": [{"text": "continue with damaged owner evidence"}],
                }
                if surface == "jsonrpc":
                    response = await client.post(
                        "/a2a",
                        headers={"a2a-version": A2A_PROTOCOL_VERSION},
                        json={
                            "jsonrpc": "2.0",
                            "id": "changed-owner-authority-resume",
                            "method": "SendMessage",
                            "params": {"message": message},
                        },
                    )
                    assert response.status_code == 200, response.text
                    assert response.json()["error"]["code"] == -32000
                    assert response.json()["error"]["message"] == "task is not accepting input"
                else:
                    response = await client.post(
                        "/a2a/message:send",
                        headers={"a2a-version": A2A_PROTOCOL_VERSION},
                        json={"message": message},
                    )
                    assert response.status_code == 409, response.text
                    assert response.json()["error"]["details"][0]["metadata"]["owaCode"] == (
                        "task_not_resumeable"
                    )

                assert "owner-evidence-secret" not in response.text and TOKEN not in response.text
                assert _common_snapshot(services, handle.invocation_id) == before
                assert _native_snapshot(services, handle, backend) == native_before
                assert (
                    not engine._active
                    and not engine._execution_leases
                    and not engine._ownership_tasks
                )
                assert len(receiver.calls) == len(receiver.rows()) == 1 and not services.model.calls
    finally:
        if original_authority is not None:
            connection = services.execution_authorizations.connection
            connection.execute(
                "INSERT INTO execution_authorities(invocation_id,fingerprint,payload,digest) "
                "VALUES (?,?,?,?) ON CONFLICT(invocation_id) DO UPDATE SET "
                "fingerprint=excluded.fingerprint,payload=excluded.payload,digest=excluded.digest",
                original_authority,
            )
            connection.commit()
        if handle is not None:
            await engine.cancel(handle, operation_id="version-fixture-cleanup")
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_new_definition_cannot_reinterpret_existing_native_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    _clock(monkeypatch, [1000.0])
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    config = _config(_definition("1.0.0"), backend)
    services = _services(tmp_path, config, receiver)
    engine = engine_type()
    _actual_native(engine, engine_name)
    app = create_app(config=config, services=services, engine=engine)
    handle: Any = None
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer " + TOKEN},
            ) as client:
                started = await client.post("/v1/invoke", json={"input": None})
                assert started.status_code == 200 and started.json()["status"] == "waiting"
                handle = services.invocations.get(started.json()["invocation_id"])
                assert handle is not None
                before = _common_snapshot(services, handle.invocation_id)
                native_before = _native_snapshot(services, handle, backend)
                with pytest.raises(WorkflowDefinitionChanged):
                    await engine.resume(handle, None, compile_workflow(_definition("2.0.0")))
                mapping = await client.post(
                    f"/v1/invocations/{handle.invocation_id}/resume",
                    json={"input": None, "migration": {"target_version": "2.0.0"}},
                )
                assert mapping.status_code == 422
                assert _common_snapshot(services, handle.invocation_id) == before
                assert _native_snapshot(services, handle, backend) == native_before
                assert len(receiver.calls) == len(receiver.rows()) == 1 and not services.model.calls
    finally:
        if handle is not None:
            await engine.cancel(handle, operation_id="version-fixture-cleanup")
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("damage", ["runtime_profile", "native_only_missing_binding"])
async def test_profile_change_or_native_only_history_cannot_adopt_new_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    damage: str,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    _clock(monkeypatch, [1000.0])
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    config = _config(_definition("1.0.0"), backend)
    services = _services(tmp_path, config, receiver)
    engine = engine_type()
    _actual_native(engine, engine_name)
    app = create_app(config=config, services=services, engine=engine)
    handle: Any = None
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer " + TOKEN},
            ) as client:
                if damage == "native_only_missing_binding":
                    # Isolate the native-only archive guard with the supported
                    # trusted engine seam. Authenticated API invocations also
                    # retain independent authority, whose earlier rejection is
                    # covered by the authenticated archive cases above.
                    plan = app.state.plan
                    handle = services.invocations.create(
                        engine=engine_name,
                        session_id=None,
                        user_id=None,
                        owner_principal=None,
                        workflow_name=plan.name,
                        workflow_version=plan.version,
                        workflow_fingerprint=plan.fingerprint,
                    )
                    started_result = await engine.invoke(plan, handle, {"original": True})
                    assert started_result.status == "waiting"
                    assert services.execution_authorizations.verify_invocation(handle) is None
                else:
                    started = await client.post("/v1/invoke", json={"input": {"original": True}})
                    assert started.status_code == 200 and started.json()["status"] == "waiting"
                    handle = services.invocations.get(started.json()["invocation_id"])
                    assert handle is not None
        native_before = _native_snapshot(services, handle, backend)
        assert any(native_before.values())
        if damage == "native_only_missing_binding":
            # Prove the actual original SDK history while its prerequisites
            # remain intact; do not invoke a gated native reader after damage.
            assert await engine._native_initial_history_exists(handle)
            connection = services.recovery_definitions.connection
            connection.execute(
                "DELETE FROM recovery_invocation_definitions WHERE invocation_id=?",
                (handle.invocation_id,),
            )
            connection.execute(
                "DELETE FROM workflow_effects WHERE identity IN "
                "(SELECT identity FROM workflow_effect_invocations WHERE invocation_id=?)",
                (handle.invocation_id,),
            )
            connection.execute(
                "DELETE FROM workflow_effect_invocations WHERE invocation_id=?",
                (handle.invocation_id,),
            )
            connection.execute(
                "DELETE FROM workflow_timers WHERE invocation_id=?", (handle.invocation_id,)
            )
            connection.execute(
                "DELETE FROM event_invocation_profiles WHERE invocation_id=?",
                (handle.invocation_id,),
            )
            connection.commit()
            services.invocations.update(handle, status="running")
        services.close()

        services = _services(tmp_path, config, receiver)
        engine = engine_type()
        # Initial SDK setup may not reinterpret stored progress or rewrite its
        # database before the original definition/profile has been admitted.
        await engine.initialize(services)
        assert _native_snapshot(services, handle, backend) == native_before
        saved = services.invocations.get(handle.invocation_id)
        assert saved is not None
        before = _common_snapshot(services, saved.invocation_id)
        if damage == "runtime_profile":
            provider = services.recovery_definitions.profile_provider

            def changed_profile(engine_name: str) -> dict[str, Any]:
                return {**provider(engine_name), "reader_contract": "unsupported-version"}

            with monkeypatch.context() as patch:
                patch.setattr(services.recovery_definitions, "profile_provider", changed_profile)
                with pytest.raises(InvocationStateError) as failure:
                    await engine.resume(saved, None, compile_workflow(_definition("1.0.0")))
                assert failure.value.details["reason"] == "runtime_profile_changed"
                report = services.recovery_definitions.diagnostic_safe_snapshot(saved)
                assert (
                    report["status"] == "unavailable"
                    and report["reason"] == "runtime_profile_changed"
                )
        else:
            with pytest.raises(InvocationStateError) as failure:
                await engine.invoke(
                    compile_workflow(_definition("1.0.0")), saved, {"replacement": True}
                )
            assert failure.value.details["reason"] == "definition_archive_missing"
        assert _common_snapshot(services, saved.invocation_id) == before
        assert _native_snapshot(services, saved, backend) == native_before
        assert not engine._active and not engine._execution_leases and not engine._ownership_tasks
        assert len(receiver.calls) == len(receiver.rows()) == 1 and not services.model.calls
    finally:
        if handle is not None:
            await engine.cancel(handle, operation_id="version-fixture-cleanup")
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_a2a_cannot_grant_an_unadvertised_archived_workflow_default_skill_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    _clock(monkeypatch, [1000.0])
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    first_config = _config(_definition("1.0.0"), backend)
    services = _services(tmp_path, first_config, receiver)
    engine = engine_type()
    _actual_native(engine, engine_name)
    app = create_app(config=first_config, services=services, engine=engine)
    handle: Any = None
    headers = {"Authorization": "Bearer " + TOKEN}
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
            ) as client:
                started = await client.post("/v1/invoke", json={"input": {"private": "old-input"}})
                assert started.status_code == 200 and started.json()["status"] == "waiting"
                handle = services.invocations.get(started.json()["invocation_id"])
                assert handle is not None
        services.close()

        other = _definition("2.0.0")
        other["document"]["name"] = "different-public-workflow"
        next_config = _config(other, backend)
        services = _services(tmp_path, next_config, receiver)
        engine = engine_type()
        app = create_app(config=next_config, services=services, engine=engine)
        async with app.router.lifespan_context(app):
            before = _common_snapshot(services, handle.invocation_id)
            native_before = _native_snapshot(services, handle, backend)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
            ) as client:
                denied = await client.post(
                    "/a2a",
                    headers={"a2a-version": A2A_PROTOCOL_VERSION},
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "SendMessage",
                        "params": {
                            "message": {
                                "role": "ROLE_USER",
                                "messageId": "denied-default-authority",
                                "taskId": handle.invocation_id,
                                "parts": [{"text": "resume under default authority"}],
                            },
                        },
                    },
                )
                assert denied.status_code == 200 and "error" in denied.json()
                assert denied.json()["error"]["message"] == "task is not accepting input"
                assert "old-input" not in denied.text
                assert _common_snapshot(services, handle.invocation_id) == before
                assert _native_snapshot(services, handle, backend) == native_before
                assert len(receiver.calls) == len(receiver.rows()) == 1 and not services.model.calls
    finally:
        if handle is not None:
            await engine.cancel(handle, operation_id="version-fixture-cleanup")
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_bound_waiting_child_continues_its_archive_with_only_catalog_v2_deployed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    now = [1000.0]
    _clock(monkeypatch, now)
    receiver = Receiver(tmp_path / "receiver.sqlite3")

    def configured(version: str) -> RuntimeConfig:
        child = _definition(version)
        child["document"]["name"] = "retained-child"
        if version == "1.0.0":
            child["do"][1]["pause"]["wait"]["seconds"] = 10
            child["do"][-1]["finish"]["set"].pop("resume")
        parent = {
            "document": {
                "dsl": "1.0.3",
                "namespace": "version-recovery",
                "name": "parent-root",
                "version": version,
            },
            "timeout": {"after": {"seconds": 300}},
            "do": [
                {
                    "child": {
                        "run": {
                            "workflow": {
                                "namespace": "version-recovery",
                                "name": "retained-child",
                                "version": "latest",
                                "input": {"original": "${ $workflow.input }"},
                            }
                        }
                    }
                },
                {
                    "finish": {
                        "set": {
                            "parent_version": version,
                            "child": "${ . }",
                            "raw_original": "${ $workflow.input }",
                        }
                    }
                },
            ],
        }
        value = _config(parent, backend).model_dump(mode="json", exclude_unset=True)
        value["workflow"]["catalog"] = [child]
        return RuntimeConfig.model_validate(value)

    config = configured("1.0.0")
    services = _services(tmp_path, config, receiver)
    engine = engine_type()
    _actual_native(engine, engine_name)
    app = create_app(config=config, services=services, engine=engine)
    original = {"request": "bound-child-original"}
    headers = {"Authorization": "Bearer " + TOKEN}
    parent: Any = None
    try:
        async with app.router.lifespan_context(app):
            await engine._timer_dispatcher.stop()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
            ) as client:
                started = await client.post("/v1/invoke", json={"input": original})
                assert started.status_code == 200 and started.json()["status"] == "waiting"
                parent = services.invocations.get(started.json()["invocation_id"])
                assert parent is not None
                registrations = services.child_waits.for_invocation(
                    parent.invocation_id, parent.workflow_fingerprint
                )
                assert len(registrations) == 1
                run_key = registrations[0].identity.key
                child_id = registrations[0].suspension.child_id
                child = services.invocations.get(child_id)
                assert child is not None and child.status == "waiting"
                child_identity = (
                    child.invocation_id,
                    child.workflow_fingerprint,
                    child.engine_execution_reference,
                    child.session_id,
                    child.owner_principal,
                )
                child_input = services.invocations.read_input(child)
                assert child_input == {"original": original}
                assert len(receiver.calls) == len(receiver.rows()) == 1
        services.close()
        config = configured("2.0.0")
        services = _services(tmp_path, config, receiver)
        engine = engine_type()
        app = create_app(config=config, services=services, engine=engine)
        async with app.router.lifespan_context(app):
            await engine._timer_dispatcher.stop()
            assert (
                services.workflow_catalog.resolve(
                    {
                        "namespace": "version-recovery",
                        "name": "retained-child",
                        "version": "latest",
                    }
                ).version
                == "2.0.0"
            )
            child = services.invocations.get(child_id)
            assert (
                child is not None and services.resolve_original_definition(child).version == "1.0.0"
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
            ) as client:
                early = await client.post(
                    f"/v1/invocations/{parent.invocation_id}/resume",
                    json={"input": {"replacement": True}},
                )
                assert early.status_code == 200 and early.json()["status"] == "waiting"
                assert len(receiver.calls) == 1
                now[0] = 1010.0
                await _advance_child(engine, child_id, now[0])
                await _advance_parent(engine, run_key)
                parent = services.invocations.get(parent.invocation_id)
                child = services.invocations.get(child_id)
                assert parent is not None and parent.status == "completed"
                assert (
                    child is not None
                    and child.status == "completed"
                    and child.workflow_version == "1.0.0"
                )
                assert (
                    child.invocation_id,
                    child.workflow_fingerprint,
                    child.engine_execution_reference,
                    child.session_id,
                    child.owner_principal,
                ) == child_identity
                assert services.invocations.read_input(child) == child_input
                assert parent.output == {
                    "parent_version": "1.0.0",
                    "child": child.output,
                    "raw_original": original,
                }
                assert child.output["version"] == "1.0.0"
                fresh = await client.post("/v1/invoke", json={"input": {"new": True}})
                assert fresh.status_code == 200 and fresh.json()["status"] == "completed"
                assert fresh.json()["output"]["parent_version"] == "2.0.0"
                assert fresh.json()["output"]["child"]["version"] == "2.0.0"
                assert len(receiver.calls) == len(receiver.rows()) == 2 and not services.model.calls
    finally:
        if parent is not None:
            await engine.cancel(parent, operation_id="version-fixture-cleanup")
        await engine.shutdown()
        services.close()
