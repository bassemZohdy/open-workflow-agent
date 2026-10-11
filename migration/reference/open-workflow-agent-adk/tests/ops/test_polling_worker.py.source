import asyncio

import pytest

from openworkflow_adk import WorkflowRegistry, load
from openworkflow_adk.internal import PostgresPollingWorker
from openworkflow_adk.ops.postgres_history import PostgresRunHistory, PostgresRunHistoryConfig
from tests.conftest import require_docker

pytestmark = [pytest.mark.integration, require_docker()]


@pytest.fixture
async def history(postgres_url):
    config = PostgresRunHistoryConfig(url=postgres_url, schema="polling", namespace_id="ns")
    store = PostgresRunHistory(config)
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


async def _make_registry() -> WorkflowRegistry:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "polled",
                "version": "1.0.0",
            },
            "do": [{"save": {"set": {"value": '"ok"'}}}],
        }
    )
    registry = WorkflowRegistry()
    registry.register(document)
    return registry


async def test_polling_worker_claims_and_executes_pending_run(history) -> None:
    registry = await _make_registry()
    await history.enqueue_run(
        "poll-run-1", "polled", input={"value": "x"}, workflow_namespace="demo"
    )

    worker = PostgresPollingWorker(
        registry=registry,
        history=history,
        poll_interval_seconds=0.1,
        lease_seconds=5.0,
    )
    result = await worker.run_once()
    assert result is not None

    record = await history.get("poll-run-1")
    assert record.status == "completed"
    assert record.state["value"] == "ok"


async def test_polling_worker_returns_none_when_no_pending_runs(history) -> None:
    registry = await _make_registry()
    worker = PostgresPollingWorker(
        registry=registry,
        history=history,
        poll_interval_seconds=0.1,
    )
    assert await worker.run_once() is None


async def test_polling_worker_runs_until_stopped(history) -> None:
    registry = await _make_registry()
    await history.enqueue_run("poll-run-2", "polled", input={}, workflow_namespace="demo")
    await history.enqueue_run("poll-run-3", "polled", input={}, workflow_namespace="demo")

    worker = PostgresPollingWorker(
        registry=registry,
        history=history,
        max_concurrency=2,
        poll_interval_seconds=0.1,
        lease_seconds=5.0,
    )
    stop = asyncio.Event()

    async def wait_then_stop() -> None:
        for _ in range(30):
            counts = await history.count_by_status()
            if counts.get("completed", 0) >= 2:
                stop.set()
                return
            await asyncio.sleep(0.1)
        stop.set()

    await asyncio.gather(worker.run_forever(stop), wait_then_stop())

    counts = await history.count_by_status()
    assert counts.get("completed", 0) >= 2


async def test_polling_worker_lease_extends_during_execution(history) -> None:
    registry = await _make_registry()
    await history.enqueue_run("lease-run", "polled", input={}, workflow_namespace="demo")

    worker = PostgresPollingWorker(
        registry=registry,
        history=history,
        poll_interval_seconds=0.1,
        lease_seconds=2.0,
        heartbeat_interval_seconds=0.2,
    )
    result = await worker.run_once()
    assert result is not None
    extended = await history.extend_lease("lease-run", worker._worker_id, lease_seconds=5.0)
    assert not extended


async def test_polling_worker_reclaims_expired_lease(postgres_url) -> None:
    config = PostgresRunHistoryConfig(url=postgres_url, schema="polling", namespace_id="reclaim")
    store = PostgresRunHistory(config)
    await store.connect()
    try:
        await store.enqueue_run("reclaim-run", "polled", input={}, workflow_namespace="demo")
        first = await store.claim_run("worker-1", lease_seconds=0.5)
        assert first is not None
        await asyncio.sleep(0.7)

        second = await store.claim_run("worker-2", lease_seconds=5.0)
        assert second is not None
        assert second.run_id == "reclaim-run"
        record = await store.get("reclaim-run")
        assert record.status == "running"
        assert record.workflow != "worker-1"
    finally:
        await store.close()
