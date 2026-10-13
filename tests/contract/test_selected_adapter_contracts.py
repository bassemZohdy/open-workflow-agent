"""Actual native/compiler/transform/embedded-broker parity, independently adapted."""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest
from _selected_adapter_fixtures import (
    ENGINES,
    RAW,
    actual_native,
    admit,
    authority,
    cases,
    create,
    definition,
    invoke,
    known,
    message,
    receipts,
    runtime,
)
from _version_recovery_worker import native_snapshot
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.errors import EventValidationError, OwaError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.workflow import compile_workflow

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("explicit_workflow_delivery")]


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "case", ["transforms", "escaped-nested", "reusable", "fork-order", "retry-null"]
)
async def test_selected_reference_patterns_keep_original_ows_semantics_and_native_history(
    tmp_path, engine_name, engine_type, backend, case
):
    async def null_response(prompt):
        return None

    model = FakeModel(null_response)
    if case == "retry-null":
        model.failures = 1
    services = runtime(tmp_path, backend, model)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    try:
        public, expected, refs = cases(case)
        plan = compile_workflow(public)
        handle = create(services, engine_name, plan)
        result = await invoke(engine, services, plan, handle)
        assert result.status == "completed", result.error
        assert result.output == expected
        assert services.invocations.read_input(handle) == RAW
        assert (
            services.execution_authorizations.verify_invocation(handle).principal.identity
            == "alice"
        )
        completed = [
            event.task_reference
            for event in services.events.events
            if event.event_type == "TaskCompleted"
        ]
        for reference in set(refs):
            assert completed.count(reference) == refs.count(reference)
        assert plan.source == public
        assert native_snapshot(services, handle, backend)["present"]
        assert all(receipt.status != "pending" for _, receipt in receipts(services, handle))
        original = known(services, handle)
        count = len(model.calls)
        assert count == (2 if case == "retry-null" else 0)
        assert await engine.resume(handle, {"replace": True}, plan) == result
        assert known(services, handle) == original and len(model.calls) == count
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("read", ["data", "envelope"])
async def test_embedded_codec_preserves_null_extensions_scope_and_duplicate_first_admission(
    tmp_path, engine_name, engine_type, backend, read
):
    services = runtime(tmp_path, backend)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    try:
        subject = str(tmp_path.name) + "-" + engine_name
        wire = message(services, subject, user="bob", role="admin")
        admitted = admit(services, wire)
        assert admit(services, wire) == admitted
        properties = wire.as_dict()
        properties.pop("specversion")
        plan = compile_workflow(
            definition(
                [
                    {
                        "receive": {
                            "listen": {
                                "to": {
                                    "one": {
                                        "with": {"type": "selected.accepted", "subject": subject}
                                    }
                                },
                                "read": read,
                            },
                        }
                    }
                ]
            )
        )
        handle = create(services, engine_name, plan)
        result = await invoke(engine, services, plan, handle)
        assert result.status == "completed", result.error
        assert result.output == ([None] if read == "data" else [properties])
        assert (
            services.event_transport.codec.encode(
                services.event_store._decode_envelope(
                    *services.effect_journal.connection.execute(
                        "SELECT envelope,digest FROM event_inbox WHERE sequence=?",
                        (admitted.sequence,),
                    ).fetchone()
                )
            )
            == wire.encoded
        )
        assert services.model.calls == []
        original = known(services, handle)
        assert await engine.resume(handle, {}, plan) == result
        assert known(services, handle) == original
        with authority_scope(authority(services, "alice")):
            with pytest.raises(EventValidationError):
                services.event_transport.admit(wire.encoded, scope="bob")
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_embedded_outbox_emit_and_scoped_listener_share_one_committed_identity(
    tmp_path, engine_name, engine_type, backend
):
    services = runtime(tmp_path, backend)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    try:
        event_id = "outbox-" + tmp_path.name + engine_name
        plan = compile_workflow(
            definition(
                [
                    {
                        "publish": {
                            "emit": {
                                "event": {
                                    "with": {
                                        "id": event_id,
                                        "source": "urn:selected:outbox",
                                        "type": "selected.accepted",
                                        "subject": event_id,
                                        "data": None,
                                        "traceparent": "exact",
                                    }
                                }
                            }
                        }
                    },
                    {"receive": {"listen": {"to": {"one": {"with": {"id": event_id}}}}}},
                ]
            )
        )
        handle = create(services, engine_name, plan)
        result = await invoke(engine, services, plan, handle)
        assert result.status == "completed" and result.output == [None], result.error
        rows = services.effect_journal.connection.execute(
            "SELECT scope,delivered FROM event_outbox WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchall()
        assert rows == [("alice", 1)]
        original = known(services, handle)
        assert services.event_transport.deliver_outbox(limit=1) == 0
        assert await engine.resume(handle, {}, plan) == result
        assert known(services, handle) == original and services.model.calls == []
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_descriptive_adk_plugin_metadata_cannot_activate_native_or_provider_behavior(
    tmp_path, engine_name, engine_type, backend
):
    services = runtime(tmp_path, backend)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    try:
        public = definition([{"ordinary": {"set": {"value": "standard"}}}])
        public["document"]["metadata"] = {
            "adk": {
                "agent": "not-a-deployment-binding",
                "plugin": "untrusted-module",
                "memoization": True,
                "selfHealer": "untrusted-execution",
            }
        }
        plan = compile_workflow(public)
        handle = create(services, engine_name, plan)
        result = await invoke(engine, services, plan, handle)
        assert result.status == "completed" and result.output == {"value": "standard"}
        assert plan.source == public
        assert services.model.calls == [] and not engine._active and not engine._execution_leases
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_disabled_executable_construct_rejects_before_native_or_business_dispatch(
    tmp_path, engine_name, engine_type, backend
):
    services = runtime(tmp_path, backend)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    try:
        with pytest.raises(OwaError):
            plan = compile_workflow(
                definition(
                    [
                        {
                            "executable": {
                                "run": {
                                    "script": {
                                        "language": "python",
                                        "code": "print('must not execute')",
                                    }
                                }
                            }
                        }
                    ]
                )
            )
            handle = create(services, engine_name, plan)
            await invoke(engine, services, plan, handle)
        assert services.model.calls == [] and not engine._active and not engine._execution_leases
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("boundary", ["known", "unknown"])
async def test_actual_bounded_memory_respects_the_enclosing_agent_receipt_on_native_reopen(
    tmp_path, engine_name, engine_type, backend, boundary
):
    entered = asyncio.Event()
    label = uuid4().hex
    model_calls = 0

    async def response(prompt):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return {
                "tool_calls": [
                    {
                        "name": "add_memory",
                        "arguments": {"text": label, "metadata": {"original": None}},
                    }
                ]
            }
        assert model_calls == 2 and len(prompt["tool_results"]) == 1
        if boundary == "unknown":
            entered.set()
            await asyncio.Event().wait()
        return {"saved": True}

    services = runtime(tmp_path, backend, FakeModel(response), bounded_memory=True)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    task = None
    try:
        plan = compile_workflow(
            definition(
                [
                    {"agent": {"call": "agent:1.0.0@default", "with": {"input": "${ . }"}}},
                    {"pause": {"wait": {"seconds": 1}}},
                    {"complete": {"set": {"done": True}}},
                ]
            )
        )
        handle = create(services, engine_name, plan)
        task = asyncio.create_task(invoke(engine, services, plan, handle))
        if boundary == "known":
            result = await task
            assert result.status == "waiting", result.error
            timer = services.timers.pending_for_invocation(handle.invocation_id, plan.fingerprint)
            assert len(timer) == 1
            due_at = timer[0].due_at
        else:
            await asyncio.wait_for(entered.wait(), 40)
            assert not task.done()
            due_at = None
        rows = services.invocations.connection.execute(
            "SELECT namespace,memory_id,text,metadata FROM bounded_memory_entries WHERE text=?",
            (label,),
        ).fetchall()
        assert len(rows) == 1 and rows[0][0].startswith("owa/principal-memory/v1:")
        assert len(services.model.calls) == 2
        original = services.execution_authorizations.verify_invocation(handle).as_value()
        before = known(services, handle)
        business = next(
            (identity, receipt)
            for identity, receipt in receipts(services, handle)
            if identity.operation == "call:agent:1.0.0@default"
        )
        assert business[1].status == ("completed" if boundary == "known" else "pending")
        assert native_snapshot(services, handle, backend)["present"]
        await engine.shutdown()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)
        services.close()

        async def forbidden(prompt):
            raise AssertionError("recovery must not repeat the model or committed memory mutation")

        services = runtime(tmp_path, backend, FakeModel(forbidden), bounded_memory=True)
        engine = engine_type()
        await engine.initialize(services)
        await engine._timer_dispatcher.stop()
        handle = services.invocations.get(handle.invocation_id)
        plan = services.resolve_original_definition(handle)
        if due_at is not None:
            while time.time() < due_at:
                await asyncio.sleep(0.01)
        result = await engine.resume(handle, {"replace": "caller"}, plan)
        if boundary == "known":
            assert result.status == "completed" and result.output == {"done": True}, result.error
        else:
            assert result.status == "faulted" and result.error["code"] == "workflow_execution_error"
            assert services.effect_journal.get(business[0]).status == "pending"
        assert services.model.calls == []
        assert (
            services.invocations.connection.execute(
                "SELECT namespace,memory_id,text,metadata FROM bounded_memory_entries WHERE text=?",
                (label,),
            ).fetchall()
            == rows
        )
        assert services.invocations.read_input(handle) == RAW
        assert services.execution_authorizations.verify_invocation(handle).as_value() == original
        after = known(services, handle)
        assert all(after[key] == proof for key, proof in before.items())
    finally:
        await engine.shutdown()
        services.close()
