"""A real deduplicating receiver distinguishes actions and reuses logical retry keys."""

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
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


def _services(path: Path, backend: str, handler: Any) -> RuntimeServices:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else path,
    )
    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    services.tools.protocols = services.protocols
    return services


def _source(tasks: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "external-keys",
            "name": "actions",
            "version": "1.0.0",
        },
        "do": tasks,
        **extra,
    }


def _action(number: Any, **keys: Any) -> dict[str, Any]:
    return {
        "call": "http",
        "with": {
            "method": "POST",
            "endpoint": "https://receiver.test/commit",
            "body": {"number": number},
            **keys,
        },
    }


def _handle(services: RuntimeServices, engine_name: str, plan: Any) -> Any:
    return services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="correlation",
        owner_principal="principal",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


class Receiver:
    def __init__(self, path: Path, *, first_fault: bool = False) -> None:
        self.path = path
        self.first_fault = first_fault
        self.calls: list[tuple[str, int, dict[str, Any]]] = []
        with closing(sqlite3.connect(path)) as database:
            database.execute(
                "CREATE TABLE business_commits("
                "operation_key TEXT PRIMARY KEY,number INTEGER NOT NULL)"
            )
            database.commit()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        key = request.headers["Idempotency-Key"]
        number = json.loads(request.content)["number"]
        with closing(sqlite3.connect(self.path)) as database:
            with database:
                created = (
                    database.execute(
                        "INSERT INTO business_commits(operation_key,number) VALUES (?,?) "
                        "ON CONFLICT(operation_key) DO NOTHING",
                        (key, number),
                    ).rowcount
                    == 1
                )
                committed = database.execute(
                    "SELECT number FROM business_commits WHERE operation_key=?", (key,)
                ).fetchone()[0]
        value = {"number": committed, "deduplicated": not created}
        self.calls.append((key, number, value))
        return httpx.Response(503 if created and self.first_fault else 200, json=value)

    def commits(self) -> list[tuple[str, int]]:
        with closing(sqlite3.connect(self.path)) as database:
            return database.execute(
                "SELECT operation_key,number FROM business_commits ORDER BY number"
            ).fetchall()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("case", ["loop", "jump", "reusable", "fork"])
async def test_receiver_commits_distinct_logical_visits_with_distinct_default_keys(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str, case: str
) -> None:
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    services = _services(tmp_path, backend, receiver)
    engine = engine_type()
    if case == "jump":
        action = _action("${ .number }")
        action["output"] = {"as": "${ {number:.number+10} }"}
        source = _source(
            [
                {"action": action},
                {
                    "again": {
                        "switch": [
                            {"repeat": {"when": "${ .number < 30 }", "then": "action"}},
                            {"finish": {"then": "end"}},
                        ]
                    }
                },
            ]
        )
    elif case == "fork":
        source = _source(
            [
                {
                    "parallel": {
                        "fork": {"branches": [{"first": _action(10)}, {"second": _action(20)}]}
                    }
                }
            ]
        )
    else:
        action = _action("${ $number }")
        extra: dict[str, Any] = {}
        if case == "reusable":
            action = {"call": "shared", "with": {"number": "${ $number }"}}
            extra = {"use": {"functions": {"shared": _action("${ .number }")}}}
        source = _source(
            [{"repeat": {"for": {"each": "number", "in": [10, 20]}, "do": [{"action": action}]}}],
            **extra,
        )
    plan = compile_workflow(source)
    try:
        await engine.initialize(services)
        handle = _handle(services, engine_name, plan)
        result = await engine.invoke(plan, handle, {"number": 10})
        assert result.status == "completed", result.error
        assert sorted(number for _, number, _ in receiver.calls) == [10, 20]
        assert len({key for key, _, _ in receiver.calls}) == 2
        assert [number for _, number in receiver.commits()] == [10, 20]
        assert all(
            value["number"] == number and not value["deduplicated"]
            for _, number, value in receiver.calls
        )
        assert not services.model.calls
        references = [
            event.task_reference
            for event in services.events.events
            if event.event_type == "TaskCompleted"
        ]
        assert references and all(reference.startswith("/do/") for reference in references)
        assert not any("visit-v1" in reference for reference in references)
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_retry_of_nested_loop_actions_reuses_each_business_key_and_commits_once(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str
) -> None:
    receiver = Receiver(tmp_path / "receiver.sqlite3", first_fault=True)
    services = _services(tmp_path, backend, receiver)
    engine = engine_type()
    plan = compile_workflow(
        _source(
            [
                {
                    "repeat": {
                        "for": {"each": "number", "in": [10, 20]},
                        "do": [
                            {
                                "retry": {
                                    "try": [{"action": _action("${ $number }")}],
                                    "catch": {"retry": {"limit": {"attempt": {"count": 1}}}},
                                }
                            }
                        ],
                    }
                }
            ]
        )
    )
    try:
        await engine.initialize(services)
        handle = _handle(services, engine_name, plan)
        result = await engine.invoke(plan, handle, {})
        assert result.status == "completed", result.error
        assert [number for _, number, _ in receiver.calls] == [10, 10, 20, 20]
        keys = [key for key, _, _ in receiver.calls]
        assert keys[0] == keys[1] and keys[2] == keys[3] and keys[0] != keys[2]
        assert [number for _, number in receiver.commits()] == [10, 20]
        assert not services.model.calls
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("field", ["operation_id", "operationId"])
async def test_explicit_business_key_remains_authoritative_across_distinct_visits(
    tmp_path: Path, engine_name: str, engine_type: Any, backend: str, field: str
) -> None:
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    services = _services(tmp_path, backend, receiver)
    engine = engine_type()
    plan = compile_workflow(
        _source(
            [
                {
                    "repeat": {
                        "for": {"each": "number", "in": [10, 20]},
                        "do": [
                            {"action": _action("${ $number }", **{field: "explicit-business-key"})}
                        ],
                    }
                }
            ]
        )
    )
    try:
        await engine.initialize(services)
        result = await engine.invoke(plan, _handle(services, engine_name, plan), {})
        assert result.status == "completed", result.error
        assert [key for key, _, _ in receiver.calls] == [
            "explicit-business-key",
            "explicit-business-key",
        ]
        assert receiver.commits() == [("explicit-business-key", 10)]
        assert not services.model.calls
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("outcome", ["completed", "pending"])
async def test_recorded_legacy_key_and_request_are_preserved_after_native_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    engine_type: Any,
    backend: str,
    outcome: str,
) -> None:
    receiver = Receiver(tmp_path / "receiver.sqlite3")
    entered, release = asyncio.Event(), asyncio.Event()

    async def receive(request: httpx.Request) -> httpx.Response:
        value = await receiver(request)
        if outcome == "pending" and len(receiver.calls) == 2:
            entered.set()
            await release.wait()
        return value

    services = _services(tmp_path, backend, receive)
    engine = engine_type()
    plan = compile_workflow(
        _source(
            [
                {
                    "repeat": {
                        "for": {"each": "number", "in": [10, 20]},
                        "do": [{"action": _action("${ $number }")}],
                    }
                }
            ]
        )
    )
    running: asyncio.Task[Any] | None = None
    try:
        await engine.initialize(services)
        handle = _handle(services, engine_name, plan)
        monkeypatch.setattr(
            engine.executor,
            "_protocol_operation_id",
            lambda _call, _payload, state: engine.executor._legacy_operation_id(state),
        )
        real_call = engine.executor._call

        async def interrupted(call: str, payload: Any, state: Any) -> Any:
            result = await real_call(call, payload, state)
            if outcome == "completed" and len(receiver.calls) == 2:
                entered.set()
                await release.wait()
            return result

        monkeypatch.setattr(engine.executor, "_call", interrupted)
        running = asyncio.create_task(engine.invoke(plan, handle, {}))
        await asyncio.wait_for(entered.wait(), timeout=30)
        before = services.effect_journal.connection.execute(
            "SELECT i.identity,e.payload FROM workflow_effect_invocations i "
            "JOIN workflow_effects e ON e.identity=i.identity "
            "WHERE i.invocation_id=? ORDER BY i.identity",
            (handle.invocation_id,),
        ).fetchall()
        old_keys = [key for key, _, _ in receiver.calls]
        assert len(old_keys) == 2 and old_keys[0] == old_keys[1]
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()
        services = _services(tmp_path, backend, receiver)
        engine = engine_type()
        await engine.initialize(services)
        handle = services.invocations.get(handle.invocation_id)
        result = await engine.resume(handle, None, plan)
        assert result.status == ("completed" if outcome == "completed" else "faulted"), result.error
        if outcome == "completed":
            assert result.output == receiver.calls[-1][2]
        else:
            assert "unresolved execution intent" in result.error["message"]
        after = services.effect_journal.connection.execute(
            "SELECT i.identity,e.payload FROM workflow_effect_invocations i "
            "JOIN workflow_effects e ON e.identity=i.identity "
            "WHERE i.invocation_id=? ORDER BY i.identity",
            (handle.invocation_id,),
        ).fetchall()
        assert after == before
        assert [key for key, _, _ in receiver.calls] == old_keys
        assert len(receiver.commits()) == 1
        assert not services.model.calls
    finally:
        release.set()
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()
