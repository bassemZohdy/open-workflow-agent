"""Trace correlation, privacy, loss budgets and asynchronous isolation."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime

import httpx
import pytest
from open_workflow_agent.config import TracingConfig
from open_workflow_agent.observability import WorkflowEvent
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.tracing import (
    ExecutionTracer,
    current_traceparent,
    invocation_trace_id,
    valid_traceparent,
)


def handle(identifier="invocation", **values):
    return ExecutionHandle(
        invocation_id=identifier,
        engine="test",
        engine_execution_reference="PRIVATE-native-reference",
        session_id="PRIVATE-session",
        user_id=None,
        workflow_name="tracing",
        workflow_version="1.0.0",
        workflow_fingerprint="fingerprint",
        **values,
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        "",
        "00-" + "0" * 32 + "-" + "1" * 16 + "-01",
        "00-" + "1" * 32 + "-" + "0" * 16 + "-01",
        "00-" + "A" * 32 + "-" + "1" * 16 + "-01",
        "ff-" + "1" * 32 + "-" + "1" * 16 + "-01",
        "01-" + "1" * 32 + "-" + "1" * 16 + "-01",
        "00-" + "1" * 32 + "-" + "1" * 16 + "-01\n",
        "00-" + "1" * 32 + "-" + "1" * 16 + "-01-extra",
        "00-" + "1" * 32 + "-" + "1" * 16 + "-gg",
    ],
)
def test_only_valid_version_zero_traceparent_is_retained(value):
    assert not valid_traceparent(value)


def test_nested_tasks_segments_and_child_lineage_have_common_correlation():
    tracer = ExecutionTracer(TracingConfig(enabled=True))
    parent = handle()
    with tracer.invocation_scope(parent):
        first = current_traceparent()
        assert valid_traceparent(first)
        assert current_traceparent(for_http=True) is None
        with tracer.task_scope(parent, "/do/0/outer"):
            outer = current_traceparent()
            with tracer.task_scope(parent, "/do/0/outer/do/0/inner"):
                inner = current_traceparent()
                event = WorkflowEvent("TaskStarted").to_cloud_event()
                assert event.as_dict()["traceparent"] == inner
            assert current_traceparent() == outer
        assert current_traceparent() == first
    assert current_traceparent() is None
    with tracer.invocation_scope(parent):
        second = current_traceparent()
        assert first != second and first.split("-")[1] == second.split("-")[1]
    with tracer.invocation_scope(handle("child", parent_invocation_id="invocation")):
        pass
    records = tracer.snapshot()
    assert records[0].parent_span_id == outer.split("-")[2]
    assert records[1].parent_span_id == first.split("-")[2]
    assert records[2].parent_span_id is None
    assert records[-1].parent_invocation_trace_id == invocation_trace_id("invocation")
    assert records[-1].trace_id != records[0].trace_id
    assert all(record.duration_seconds >= 0 for record in records)
    assert all(
        datetime.fromisoformat(record.started_at).utcoffset().total_seconds() == 0
        for record in records
    )
    assert "PRIVATE" not in repr(records)
    assert tracer.capabilities()["enabled"] is True


def test_disabled_runtime_cannot_inherit_another_runtimes_context():
    enabled = ExecutionTracer(TracingConfig(enabled=True, propagate_http=True))
    disabled = ExecutionTracer(TracingConfig())
    with enabled.invocation_scope(handle()):
        active = current_traceparent()
        with disabled.invocation_scope(handle("other")):
            assert current_traceparent() is None
            assert "traceparent" not in WorkflowEvent("TaskStarted").to_cloud_event().as_dict()
        assert current_traceparent() == active
    assert not disabled.snapshot()
    assert disabled.capabilities()["httpPropagation"] is False


def test_active_and_retained_budgets_are_observable_without_breaking_execution():
    tracer = ExecutionTracer(TracingConfig(enabled=True, max_active_spans=1, max_spans=1))
    with tracer.invocation_scope(handle()):
        active = current_traceparent()
        with tracer.task_scope(handle(), "/do/0/skipped-trace"):
            assert current_traceparent() is None
        assert current_traceparent() == active
    with tracer.invocation_scope(handle("second")):
        pass
    assert len(tracer.snapshot()) == 1
    assert tracer.dropped_spans == tracer.evicted_spans == 1
    assert tracer.snapshot()[0].invocation_id == "second"
    too_small = ExecutionTracer(TracingConfig(enabled=True, max_retained_bytes=256))
    with too_small.invocation_scope(handle()):
        pass
    assert not too_small.snapshot() and too_small.dropped_spans == 1
    byte_bound = ExecutionTracer(TracingConfig(enabled=True, max_retained_bytes=600))
    for number in range(3):
        with byte_bound.invocation_scope(handle(str(number))):
            pass
    assert len(byte_bound.snapshot()) == 1 and byte_bound.evicted_spans == 2


@pytest.mark.parametrize(
    "status,outcome",
    [("faulted", "error"), ("cancelled", "cancelled"), ("private-status", "completed")],
)
def test_invocation_normal_return_records_canonical_fault_cancel_or_unknown(status, outcome):
    tracer = ExecutionTracer(TracingConfig(enabled=True))
    invocation = handle(status=status)
    with tracer.invocation_scope(invocation):
        pass
    record = tracer.snapshot()[0]
    assert record.outcome == outcome
    assert record.invocation_status == ("unknown" if status == "private-status" else status)


@pytest.mark.parametrize("reference", ["x" * 4097, "\ud800"], ids=["oversized", "surrogate"])
def test_oversized_or_invalid_reference_is_omitted_from_fixed_projection(reference):
    tracer = ExecutionTracer(TracingConfig(enabled=True))
    with tracer.task_scope(handle(), reference):
        pass
    assert tracer.snapshot()[0].task_reference is None


@pytest.mark.asyncio
async def test_concurrent_error_and_cancel_cleanup_do_not_capture_payload_or_exception():
    tracer = ExecutionTracer(TracingConfig(enabled=True))
    barrier = asyncio.Event()

    async def execute(identifier, outcome):
        with tracer.invocation_scope(handle(identifier)):
            before = current_traceparent()
            await barrier.wait()
            await asyncio.sleep(0)
            assert current_traceparent() == before
            if outcome == "error":
                raise ValueError("SECRET error prompt and credential")
            if outcome == "cancel":
                raise asyncio.CancelledError("SECRET cancel input")

    tasks = [
        asyncio.create_task(execute(str(index), outcome))
        for index, outcome in enumerate(["error", "cancel", "completed"])
    ]
    barrier.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert current_traceparent() is None
    records = tracer.snapshot()
    assert len({record.trace_id for record in records}) == 3
    assert {record.outcome for record in records} == {"error", "cancelled", "completed"}
    assert "SECRET" not in repr([asdict(record) for record in records])
    with tracer.invocation_scope(handle("after-failures")):
        assert valid_traceparent(current_traceparent())


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,propagate", [(False, True), (True, False), (True, True)])
async def test_http_context_is_explicit_opt_in_and_cannot_replace_caller_header(enabled, propagate):
    requests = []

    async def peer(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    tracer = ExecutionTracer(TracingConfig(enabled=enabled, propagate_http=propagate))
    client = HttpClient(transport=httpx.MockTransport(peer))
    with tracer.invocation_scope(handle()):
        expected = current_traceparent()
        assert await client.request("GET", "https://service.test/") == {"ok": True}
        await client.request("GET", "https://service.test/", headers={"TraceParent": "explicit"})
    assert requests[0].headers.get("traceparent") == (expected if enabled and propagate else None)
    assert requests[1].headers["traceparent"] == "explicit"
    await client.request("GET", "https://service.test/")
    assert "traceparent" not in requests[-1].headers


@pytest.mark.parametrize(
    "config",
    [
        {"max_spans": 0},
        {"max_active_spans": 0},
        {"max_retained_bytes": 255},
        {"exporter": "https://untrusted.test"},
    ],
)
def test_invalid_or_unsupported_tracing_configuration_fails_closed(config):
    with pytest.raises(ValueError):
        TracingConfig.model_validate(config)
