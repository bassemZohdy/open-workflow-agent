"""Diagnostic restart, privacy, retention and physical cross-process delivery."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from open_workflow_agent.config import LifecycleHistoryConfig, RuntimeConfig
from open_workflow_agent.errors import ConfigurationError
from open_workflow_agent.lifecycle_history import LifecycleHistory, LifecycleHistoryError
from open_workflow_agent.observability import (
    InMemoryEventSink,
    LifecycleCloudEventSink,
    WorkflowEvent,
)
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.streaming import StreamLimits, lifecycle_sse_stream


@pytest.fixture(params=["sqlite", "postgresql"])
def selected_history(request, tmp_path):
    database = (
        os.getenv("OWA_TEST_POSTGRES_URL")
        if request.param == "postgresql"
        else str(tmp_path / "history.sqlite3")
    )
    if database is None:
        pytest.skip("actual PostgreSQL datasource is required")
    namespace = "owa_history_" + uuid4().hex
    opened = []

    def create(**values):
        config = LifecycleHistoryConfig(enabled=True, **values)
        store = LifecycleHistory(database, config, namespace=namespace)
        opened.append(store)
        return store

    yield database, namespace, create
    for store in opened:
        store.close()
    if request.param == "postgresql":
        import psycopg
        from psycopg import sql

        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(namespace)))


def event(identifier, **values):
    return WorkflowEvent(
        "WorkflowCompleted", event_id=identifier, invocation_id="safe-invocation", **values
    ).to_cloud_event()


def rows(history):
    return (
        history.connection.execute("SELECT * FROM lifecycle_history ORDER BY sequence").fetchall(),
        history.connection.execute("SELECT * FROM lifecycle_history_counter").fetchall(),
    )


def test_restart_retains_exact_projection_and_excludes_raw_errors(selected_history):
    _, _, create = selected_history
    first = create()
    original = event(
        "first",
        error={
            "code": "controlled",
            "message": "must-not-persist",
            "details": {"checkpoint": "private"},
        },
    )
    first.append(original)
    first.close()
    restored = create()
    assert restored.snapshot(1)[0].as_dict() == original.as_dict()
    assert "must-not-persist" not in repr(rows(restored))
    assert "checkpoint" not in repr(rows(restored))


def test_count_and_byte_retention_have_explicit_replay_gaps(selected_history):
    _, _, create = selected_history
    history = create(max_events=2)
    for identifier in ("one", "two", "three"):
        history.append(event(identifier))
    assert [value.id for value in history.snapshot(2)] == ["two", "three"]
    with pytest.raises(LookupError):
        history.replay("one")
    with pytest.raises(BufferError):
        history.after(0, 2)
    bounded = create(max_events=10, max_event_bytes=1024, max_retained_bytes=1200)
    for identifier in ("four", "five", "six"):
        bounded.append(event(identifier, reason="r" * 300))
    retained = rows(bounded)[0]
    assert len(retained) < 3 and sum(row[3] for row in retained) <= 1200
    assert bounded.snapshot(10)[-1].id == "six"


def test_duplicate_is_read_only_and_conflict_or_oversize_cannot_mutate(selected_history):
    _, _, create = selected_history
    history = create(max_event_bytes=1024)
    value = event("stable")
    history.append(value)
    before = rows(history)
    history.append(value)
    assert rows(history) == before
    for invalid in (
        replace(value, data={**value.data, "status": "changed"}),
        event("large", reason="x" * 2000),
    ):
        with pytest.raises(LifecycleHistoryError):
            history.append(invalid)
        assert rows(history) == before


def test_forged_projection_rejects_without_repair_or_private_disclosure(selected_history):
    _, _, create = selected_history
    history = create()
    value = event("damaged").as_dict()
    history.append(event("damaged"))
    value["data"]["private"] = "must-not-persist"
    encoded = json.dumps(value)
    history.connection.execute(
        "UPDATE lifecycle_history SET payload=?,payload_bytes=?", (encoded, len(encoded.encode()))
    )
    history.connection.commit()
    before = rows(history)
    with pytest.raises(LifecycleHistoryError, match="record is invalid") as raised:
        history.snapshot(1)
    assert "must-not-persist" not in str(raised.value) and rows(history) == before


def test_independent_writers_serialize_one_commit_order(selected_history):
    _, _, create = selected_history
    history = create()
    writers = [create() for _ in range(4)]

    def write(index):
        for iteration in range(5):
            writers[index].append(event(f"{index}-{iteration}"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(4)))
    retained = rows(history)[0]
    assert [row[0] for row in retained] == list(range(1, 21))
    assert len({row[1] for row in retained}) == 20


@pytest.mark.asyncio
async def test_stream_reads_committed_event_after_physical_producer_kill(selected_history):
    database, namespace, create = selected_history
    observer = create()
    sink = LifecycleCloudEventSink(InMemoryEventSink(), history=observer)
    stream = lifecycle_sse_stream(sink, limits=StreamLimits(max_events=1, timeout_seconds=5))
    writer = Path(__file__).resolve().parents[1] / "_lifecycle_history_writer.py"
    process = subprocess.Popen(
        [sys.executable, str(writer)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(
            json.dumps(
                {
                    "database": str(database),
                    "namespace": namespace,
                    "config": observer.config.model_dump(),
                    "event_id": "physical",
                }
            )
            + "\n"
        )
        process.stdin.flush()
        line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), timeout=20)
        assert line.strip() == "committed"
        process.kill()
        await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=5)
        frame = await asyncio.wait_for(anext(stream), timeout=5)
        assert frame.startswith(b"id: physical\n") and b"must-not-persist" not in frame
        assert b"event_limit" in await anext(stream)
        replay, subscription = sink.subscribe(last_event_id="physical")
        assert replay == []
        subscription.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for handle in (process.stdin, process.stdout, process.stderr):
            if handle is not None:
                handle.close()
        await stream.aclose()
    assert sink._history_subscribers == set()


@pytest.mark.asyncio
async def test_slow_durable_observer_keeps_bounded_backpressure(selected_history):
    _, _, create = selected_history
    history = create()
    sink = LifecycleCloudEventSink(InMemoryEventSink(), history=history)
    stream = lifecycle_sse_stream(
        sink, limits=StreamLimits(max_events=5, queue_size=1, timeout_seconds=1)
    )
    history.append(event("one"))
    history.append(event("two"))
    assert b'"reason":"backpressure"' in await anext(stream)
    await stream.aclose()
    assert sink._history_subscribers == set()


def test_enabled_history_requires_persistent_storage():
    config = RuntimeConfig.model_validate(
        {"persistence": {"database": ":memory:"}, "observability": {"history": {"enabled": True}}}
    )
    with pytest.raises(ConfigurationError, match="persistent common storage"):
        RuntimeServices(config)


@pytest.mark.parametrize(
    "damage",
    [
        "counter-negative",
        "counter-mismatch",
        "size",
        "envelope",
        "schema",
        "error-details",
        "progress-fields",
        "subject",
        "status",
        "duration",
        "attempt",
        "id-newline",
    ],
)
def test_damaged_history_metadata_rejects_read_only(selected_history, damage):
    _, _, create = selected_history
    history = create()
    history.append(event("retained"))
    if damage.startswith("counter-"):
        history.connection.execute(
            "UPDATE lifecycle_history_counter SET value=?",
            (-1 if damage == "counter-negative" else 2,),
        )
    else:
        payload = event("retained").as_dict()
        if damage == "envelope":
            payload = {"id": "retained"}
        elif damage == "schema":
            payload["dataschema"] = "urn:wrong"
        elif damage == "error-details":
            payload["data"]["error"] = {"code": "controlled", "message": "must-not-disclose"}
        elif damage == "progress-fields":
            payload["data"]["progress"] = {"private": "must-not-disclose"}
        elif damage == "subject":
            payload["subject"] = {"private": "must-not-disclose"}
        elif damage == "status":
            payload["data"]["status"] = {"private": "must-not-disclose"}
        elif damage == "duration":
            payload["data"]["duration"] = "must-not-disclose"
        elif damage == "attempt":
            payload["data"]["attempt"] = True
        elif damage == "id-newline":
            payload["id"] = payload["data"]["event_id"] = "retained\ninjected"
        encoded = json.dumps(payload)
        history.connection.execute(
            "UPDATE lifecycle_history SET payload=?,payload_bytes=?",
            (encoded, len(encoded.encode()) + (damage == "size")),
        )
    history.connection.commit()
    before = rows(history)
    with pytest.raises(LifecycleHistoryError) as raised:
        history.snapshot(1)
    assert "must-not-disclose" not in str(raised.value)
    assert rows(history) == before


def test_invalid_write_storage_failure_and_closed_history_are_sanitized(selected_history):
    _, _, create = selected_history
    history = create()
    before = rows(history)
    with pytest.raises(LifecycleHistoryError):
        history.append(event("nonfinite", duration=float("nan")))
    assert rows(history) == before
    with pytest.raises(ValueError):
        history.snapshot(0)
    history.connection.execute("DROP TABLE lifecycle_history_counter")
    history.connection.commit()
    with pytest.raises(LifecycleHistoryError, match="storage is unavailable"):
        history.snapshot(1)
    history.close()
    with pytest.raises(LifecycleHistoryError, match="history is closed"):
        history.snapshot(1)


@pytest.mark.asyncio
async def test_durable_observer_timeout_and_capacity_release(selected_history):
    _, _, create = selected_history
    history = create()
    sink = LifecycleCloudEventSink(InMemoryEventSink(), history=history, max_subscribers=1)
    stream = lifecycle_sse_stream(sink, limits=StreamLimits(timeout_seconds=0.01))
    with pytest.raises(RuntimeError, match="maximum lifecycle"):
        sink.subscribe()
    assert b'"reason":"timeout"' in await anext(stream)
    await stream.aclose()
    assert sink._history_subscribers == set()
    _, subscription = sink.subscribe()
    subscription.close()
    with pytest.raises(RuntimeError, match="subscription is closed"):
        await subscription.receive(timeout=1)
