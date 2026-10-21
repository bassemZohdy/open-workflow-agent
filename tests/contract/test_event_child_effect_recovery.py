"""Event publication and actual child invocations survive the native task gap."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import OwaError
from open_workflow_agent.events import EventEnvelope, InMemoryEventBus
from open_workflow_agent.observability import InMemoryEventSink
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import ExecutionState, compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


class _ObservedEventBus(InMemoryEventBus):
    """Actual process-local publication with a deterministic interruption point."""

    def __init__(self, outcome: str) -> None:
        super().__init__()
        self.outcome = outcome
        self.attempts = 0
        self.committed = asyncio.Event()

    async def publish(self, properties: Mapping[str, Any], *, default_source: str) -> EventEnvelope:
        self.attempts += 1
        if self.outcome == "fault":
            raise ValueError("known event rejection")
        envelope = await super().publish(properties, default_source=default_source)
        self.committed.set()
        if self.outcome == "pending":
            await asyncio.Event().wait()
        return envelope


def _document(name: str) -> dict[str, str]:
    return {"dsl": "1.0.3", "namespace": "recovery-tests", "name": name, "version": "1.0.0"}


def _child(endpoint: str = "https://service.test/child") -> dict[str, Any]:
    return {
        "document": _document("child"),
        "do": [
            {
                "action": {
                    "call": "http",
                    "with": {"method": "POST", "endpoint": endpoint, "body": "${ . }"},
                }
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "kind,request_change",
    [
        ("emit", "none"),
        ("emit", "input"),
        ("workflow", "none"),
        ("workflow", "input"),
        ("workflow", "definition"),
    ],
)
@pytest.mark.parametrize("outcome", ["value", "null", "fault", "pending"])
async def test_event_or_child_receipt_prevents_another_recovery_dispatch(
    tmp_path: Path,
    engine_name: str,
    engine_type: Any,
    backend: str,
    kind: str,
    outcome: str,
    request_change: str,
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    bus = _ObservedEventBus(outcome)
    calls: list[str] = []
    committed = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Idempotency-Key"])
        committed.set()
        if outcome == "pending":
            await asyncio.Event().wait()
        return httpx.Response(
            503 if outcome == "fault" else 200,
            json=None if outcome == "null" else {"accepted": True},
            content=b"null" if outcome == "null" else None,
            headers={"Content-Type": "application/json"},
        )

    def services(*, changed_child: bool = False) -> RuntimeServices:
        result = RuntimeServices(
            config, model=FakeModel(), database_root=None if datasource else tmp_path
        )
        result.event_bus = bus
        result.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
        result.tools.protocols = result.protocols
        result.workflow_catalog.register(
            _child(
                "https://service.test/changed" if changed_child else "https://service.test/child"
            )
        )
        return result

    operation = (
        {"emit": {"event": {"with": {"type": "case.created", "data": "${ .request }"}}}}
        if kind == "emit"
        else {
            "run": {
                "workflow": {
                    "namespace": "recovery-tests",
                    "name": "child",
                    "version": "1.0.0",
                    "input": {"request": "${ .request }"},
                }
            }
        }
    )
    plan = compile_workflow(
        {"document": _document("parent-" + kind), "do": [{"action": operation}]}
    )
    first = services()
    engine = engine_type()
    await engine.initialize(first)
    handle = first.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="parent-user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    method = "_run_emit" if kind == "emit" else "_run_subworkflow"
    perform = getattr(engine.executor, method)
    reached = asyncio.Event()
    expected_error: dict[str, Any] | None = None
    child_id: str | None = None

    async def interrupted(definition: Any, state: ExecutionState) -> Any:
        nonlocal expected_error
        try:
            result = await perform(definition, state)
        except OwaError as exc:
            expected_error = exc.as_dict()
            reached.set()
            await asyncio.Event().wait()
            raise
        reached.set()
        await asyncio.Event().wait()
        return result

    setattr(engine.executor, method, interrupted)
    running = asyncio.create_task(engine.invoke(plan, handle, {"request": "original"}))
    try:
        await asyncio.wait_for(
            (bus.committed if kind == "emit" else committed).wait()
            if outcome == "pending"
            else reached.wait(),
            30,
        )
        assert handle.status == "running"
        assert (bus.attempts if kind == "emit" else len(calls)) == 1
        if kind == "workflow":
            started = [
                event
                for event in cast(InMemoryEventSink, first.events).events
                if event.event_type == "WorkflowStarted"
                and event.parent_invocation_id == handle.invocation_id
            ]
            assert len(started) == 1
            child_id = started[0].invocation_id
            assert child_id is not None and child_id != handle.invocation_id
            child = first.invocations.get(child_id)
            assert child is not None and child.parent_task_reference == "/do/0/action"
            assert (
                child.owner_principal == handle.owner_principal and child.user_id == "parent-user"
            )
            assert child.status == (
                "running"
                if outcome == "pending"
                else "faulted"
                if outcome == "fault"
                else "completed"
            )
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 15)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        first.close()

    restarted = services(changed_child=request_change == "definition")
    recovered = engine_type()
    await recovered.initialize(restarted)
    if request_change == "input":
        original = getattr(recovered.executor, method)

        async def changed(definition: Any, state: ExecutionState) -> Any:
            state.data = {"request": "changed"}
            return await original(definition, state)

        setattr(recovered.executor, method, changed)
    loaded = restarted.invocations.get(handle.invocation_id)
    assert loaded is not None and loaded.status == "running"
    try:
        result = await asyncio.wait_for(recovered.resume(loaded, {}, plan), 15)
        assert (bus.attempts if kind == "emit" else len(calls)) == 1, (
            "recovery must not repeat known or uncertain event/child dispatch"
        )
        assert not any(
            event.event_type == "WorkflowStarted"
            and event.parent_invocation_id == handle.invocation_id
            for event in cast(InMemoryEventSink, restarted.events).events
        )
        if request_change == "definition":
            # A newer mutable catalog selection cannot reinterpret the admitted
            # child's original source or replace its known/uncertain outcome.
            original_child = restarted.invocations.get(child_id)
            assert original_child is not None
            archived = restarted.resolve_original_definition(original_child)
            assert archived.fingerprint == compile_workflow(_child()).fingerprint
            assert (
                archived.fingerprint
                != compile_workflow(_child("https://service.test/changed")).fingerprint
            )
            assert restarted.invocations.read_input(original_child) == {"request": "original"}
        if request_change == "input" or outcome == "pending":
            assert result.status == "faulted" and result.error is not None
            failure = result.error
            assert ("differs" if request_change == "input" else "unresolved") in failure["message"]
            if outcome == "pending" and kind == "workflow":
                rows = restarted.effect_journal.connection.execute(
                    "SELECT i.payload,e.status FROM workflow_effect_invocations i "
                    "JOIN workflow_effects e ON e.identity=i.identity WHERE i.invocation_id=?",
                    (handle.invocation_id,),
                ).fetchall()
                pending = [
                    row[1] for row in rows if json.loads(row[0])["operation"] == "run:workflow"
                ]
                assert pending == ["pending"]
        elif outcome == "fault":
            assert expected_error is not None
            assert result.status == "faulted" and result.error == {
                **expected_error,
                "details": {**expected_error["details"], "instance": "/do/0/action"},
            }
        else:
            assert result.status == "completed", result.error
            assert result.output == (
                {"request": "original"}
                if kind == "emit"
                else None
                if outcome == "null"
                else {"accepted": True}
            )
            if kind == "emit":
                published = bus.published[0]
                events = [
                    event
                    for event in cast(InMemoryEventSink, restarted.events).events
                    if event.event_type == "EventEmitted"
                ]
                assert len(events) == 1 and events[0].event_id == published.id
                assert published.data == "original" and len(bus.published) == 1
                assert bus.durable is False
        if child_id is not None:
            child = restarted.invocations.get(child_id)
            assert child is not None and child.parent_invocation_id == handle.invocation_id
        duplicate = await recovered.resume(loaded, None, plan)
        assert duplicate.as_dict() == result.as_dict()
        assert (bus.attempts if kind == "emit" else len(calls)) == 1
    finally:
        await recovered.shutdown()
        restarted.close()
