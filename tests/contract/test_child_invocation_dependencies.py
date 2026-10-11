"""Known child outcomes depend on actual common child records and input."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError, OwaError
from open_workflow_agent.observability import InMemoryEventSink
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import ExecutionState, compile_workflow

CASES = [
    (outcome, damage, "task-gap")
    for outcome in ("value", "null", "fault")
    for damage in ("clean", "deleted")
] + [
    ("value", damage, "waiting")
    for damage in (
        "clean",
        "deleted",
        "output",
        "parent",
        "input",
        "missing-input",
        "reference",
        "pending",
        "leaf-receipt",
        "index",
        "old-binding",
    )
]


def _document(name: str) -> dict[str, str]:
    return {"dsl": "1.0.3", "namespace": "child-dependencies", "name": name, "version": "1.0.0"}


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("outcome,damage,point", CASES)
async def test_child_invocation_dependency_reconciles_before_parent_recovery(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    outcome: str,
    damage: str,
    point: str,
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Idempotency-Key"])
        return httpx.Response(
            503 if outcome == "fault" else 200,
            content=b"null" if outcome == "null" else b'{"accepted":true}',
            headers={"Content-Type": "application/json"},
        )

    def services() -> RuntimeServices:
        result = RuntimeServices(
            config, model=FakeModel(), database_root=None if datasource else tmp_path
        )
        result.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
        result.tools.protocols = result.protocols
        result.workflow_catalog.register(
            {
                "document": _document("child"),
                "do": [
                    {
                        "action": {
                            "call": "http",
                            "with": {
                                "method": "POST",
                                "endpoint": "https://service.test/child",
                                "body": "${ . }",
                            },
                        }
                    }
                ],
            }
        )
        return result

    tasks: list[dict[str, Any]] = [
        {
            "child": {
                "run": {
                    "workflow": {
                        "namespace": "child-dependencies",
                        "name": "child",
                        "version": "1.0.0",
                        "input": {"request": "${ .request }"},
                    }
                }
            }
        }
    ]
    if point == "waiting":
        tasks += [{"pause": {"wait": {"seconds": 300}}}, {"finish": {"set": {"done": True}}}]
    plan = compile_workflow({"document": _document("parent"), "do": tasks})
    first = services()
    engine = engine_type()
    await engine.initialize(first)
    handle = first.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="child-user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
        owner_principal="principal",
    )
    reached = asyncio.Event()
    expected_error: dict[str, Any] | None = None
    if point == "task-gap":
        perform = engine.executor._run_subworkflow

        async def interrupted(definition: Any, state: ExecutionState) -> Any:
            nonlocal expected_error
            try:
                output = await perform(definition, state)
            except OwaError as error:
                expected_error = error.as_dict()
                reached.set()
                await asyncio.Event().wait()
                raise
            reached.set()
            await asyncio.Event().wait()
            return output

        engine.executor._run_subworkflow = interrupted
    running = asyncio.create_task(engine.invoke(plan, handle, {"request": "original"}))
    try:
        async with asyncio.timeout(20):
            if point == "task-gap":
                await reached.wait()
            else:
                while handle.status != "waiting":
                    if running.done():
                        pytest.fail(str(running.result()))
                    await asyncio.sleep(0.005)
        started = [
            event
            for event in cast(InMemoryEventSink, first.events).events
            if event.event_type == "WorkflowStarted"
            and event.parent_invocation_id == handle.invocation_id
        ]
        assert len(started) == 1 and len(calls) == 1
        child_id = started[0].invocation_id
        assert child_id is not None
        child = first.invocations.get(child_id)
        assert child is not None and child.status == (
            "faulted" if outcome == "fault" else "completed"
        )
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        first.close()

    restarted = services()
    recovered = engine_type()
    await recovered.initialize(restarted)
    child = restarted.invocations.get(child_id)
    assert child is not None and child._persisted_payload is not None
    connection = restarted.invocations.connection
    if damage == "deleted":
        connection.execute("DELETE FROM invocations WHERE invocation_id = ?", (child_id,))
        assert restarted.invocations.get(child_id) is None
    elif damage in {"output", "parent", "reference", "pending"}:
        payload = json.loads(child._persisted_payload)
        field, value = {
            "output": ("output", {"forged": True}),
            "parent": ("parent_task_reference", "/forged"),
            "reference": ("engine_execution_reference", "forged"),
            "pending": ("status", "running"),
        }[damage]
        payload[field] = value
        connection.execute(
            "UPDATE invocations SET payload = ?, status = ? WHERE invocation_id = ?",
            (json.dumps(payload), payload["status"], child_id),
        )
        reread = restarted.invocations.get(child_id)
        assert reread is not None and getattr(reread, field) == value
    elif damage == "input":
        connection.execute(
            "UPDATE invocation_inputs SET payload = ? WHERE invocation_id = ?",
            (json.dumps({"request": "forged"}), child_id),
        )
        assert restarted.invocations.read_input(child) == {"request": "forged"}
    elif damage == "missing-input":
        connection.execute("DELETE FROM invocation_inputs WHERE invocation_id = ?", (child_id,))
        assert (
            connection.execute(
                "SELECT payload FROM invocation_inputs WHERE invocation_id = ?", (child_id,)
            ).fetchone()
            is None
        )
    elif damage == "leaf-receipt":
        rows = connection.execute(
            "SELECT identity FROM workflow_effect_invocations WHERE invocation_id = ?", (child_id,)
        ).fetchall()
        assert len(rows) == 1
        connection.execute("DELETE FROM workflow_effects WHERE identity = ?", (rows[0][0],))
        assert (
            connection.execute(
                "SELECT payload FROM workflow_effects WHERE identity = ?", (rows[0][0],)
            ).fetchone()
            is None
        )
    elif damage == "index":
        connection.execute(
            "UPDATE workflow_effect_invocations SET fingerprint = ? WHERE invocation_id = ?",
            ("forged", handle.invocation_id),
        )
        assert (
            connection.execute(
                "SELECT fingerprint FROM workflow_effect_invocations WHERE invocation_id = ?",
                (handle.invocation_id,),
            ).fetchone()[0]
            == "forged"
        )
    elif damage == "old-binding":
        rows = connection.execute(
            "SELECT identity FROM workflow_effect_invocations WHERE invocation_id = ?",
            (handle.invocation_id,),
        ).fetchall()
        assert len(rows) == 1
        payload = json.loads(
            connection.execute(
                "SELECT payload FROM workflow_effects WHERE identity = ?", (rows[0][0],)
            ).fetchone()[0]
        )
        assert payload.pop("child_invocation")["invocation_id"] == child_id
        connection.execute(
            "UPDATE workflow_effects SET payload = ? WHERE identity = ?",
            (json.dumps(payload), rows[0][0]),
        )
    connection.commit()
    loaded = restarted.invocations.get(handle.invocation_id)
    assert loaded is not None
    before = loaded._persisted_payload
    try:
        if damage != "clean":
            with pytest.raises(InvocationStateError):
                await asyncio.wait_for(recovered.resume(loaded, {}, plan), 20)
            unchanged = restarted.invocations.get(handle.invocation_id)
            assert unchanged is not None and unchanged._persisted_payload == before
        else:
            result = await asyncio.wait_for(recovered.resume(loaded, {}, plan), 20)
            if outcome == "fault":
                assert expected_error is not None
                assert result.status == "faulted" and result.error == {
                    **expected_error,
                    "details": {**expected_error["details"], "instance": "/do/0/child"},
                }
            else:
                assert result.status == "completed", result.error
                assert result.output == (
                    {"done": True}
                    if point == "waiting"
                    else None
                    if outcome == "null"
                    else {"accepted": True}
                )
            duplicate = await recovered.resume(loaded, None, plan)
            assert duplicate.as_dict() == result.as_dict()
        assert len(calls) == 1
        assert not any(
            event.event_type == "WorkflowStarted"
            and event.parent_invocation_id == handle.invocation_id
            for event in cast(InMemoryEventSink, restarted.events).events
        )
    finally:
        await recovered.shutdown()
        restarted.close()
