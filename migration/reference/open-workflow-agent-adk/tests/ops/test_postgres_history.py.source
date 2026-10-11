import pytest

from openworkflow_adk import PostgresRunHistory, load, run_workflow
from openworkflow_adk.ops.postgres_history import PostgresRunHistoryConfig
from tests.conftest import require_docker

pytestmark = [pytest.mark.integration, require_docker()]


@pytest.fixture
async def history(postgres_url):
    config = PostgresRunHistoryConfig(
        url=postgres_url,
        schema="test_owf",
        namespace_id="test_ns",
    )
    store = PostgresRunHistory(config)
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


async def test_postgres_history_records_completed_run(history) -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "history",
                "version": "1.0.0",
            },
            "do": [{"save": {"set": {"value": '"ok"'}}}],
        }
    )

    await run_workflow(document, session_id="run-1", history=history)

    record = await history.get("run-1")
    assert record.status == "completed"
    assert record.state["value"] == "ok"
    assert record.workflow == "history"
    assert record.region is None


async def test_postgres_history_tracks_region_and_namespace(postgres_url) -> None:
    config = PostgresRunHistoryConfig(
        url=postgres_url,
        schema="test_owf",
        namespace_id="regioned",
    )
    store = PostgresRunHistory(config)
    await store.connect()
    try:
        record = await store.start("run-region", "wf", {"x": 1}, region="us-east-1")
        assert record.region == "us-east-1"

        fetched = await store.get("run-region")
        assert fetched.region == "us-east-1"

        counts = await store.count_by_status()
        assert counts.get("running") == 1
    finally:
        await store.close()


async def test_postgres_history_namespace_isolation(postgres_url) -> None:
    ns_a = PostgresRunHistory(
        PostgresRunHistoryConfig(url=postgres_url, schema="test_owf", namespace_id="ns-a")
    )
    ns_b = PostgresRunHistory(
        PostgresRunHistoryConfig(url=postgres_url, schema="test_owf", namespace_id="ns-b")
    )
    await ns_a.connect()
    await ns_b.connect()
    try:
        await ns_a.start("shared-id", "wf", {"owner": "a"})
        with pytest.raises(KeyError):
            await ns_b.get("shared-id")

        await ns_b.start("shared-id", "wf", {"owner": "b"})
        record_a = await ns_a.get("shared-id")
        record_b = await ns_b.get("shared-id")
        assert record_a.state["owner"] == "a"
        assert record_b.state["owner"] == "b"
    finally:
        await ns_a.close()
        await ns_b.close()


async def test_postgres_history_lists_and_filters(postgres_url) -> None:
    config = PostgresRunHistoryConfig(
        url=postgres_url, schema="test_owf", namespace_id="list-filter"
    )
    store = PostgresRunHistory(config)
    await store.connect()
    try:
        await store.start("run-a", "wf-a", {})
        await store.start("run-b", "wf-b", {})
        (await store.get("run-b")).status  # noqa: B018
        await store.finish("run-b", state={}, output="done")

        all_records = await store.list_runs()
        assert len(all_records) == 2

        completed = await store.list_runs(status="completed")
        assert len(completed) == 1
        assert completed[0].run_id == "run-b"

        wf_a = await store.list_runs(workflow="wf-a")
        assert len(wf_a) == 1
        assert wf_a[0].run_id == "run-a"
    finally:
        await store.close()


async def test_postgres_history_migrations_are_idempotent(postgres_url) -> None:
    config = PostgresRunHistoryConfig(url=postgres_url, schema="idempotent", namespace_id="ns")
    first = PostgresRunHistory(config)
    await first.connect()
    await first.close()

    second = PostgresRunHistory(config)
    await second.connect()
    await second.start("idemp", "wf", {})
    assert (await second.get("idemp")).workflow == "wf"
    await second.close()


async def test_postgres_history_records_step_attempts(history) -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "steps",
                "version": "1.0.0",
            },
            "do": [
                {"first": {"set": {"value": '"one"'}}},
                {"second": {"set": {"value": '"two"'}}},
            ],
        }
    )

    await run_workflow(document, session_id="run-steps", history=history)

    attempts = await history.list_step_attempts("run-steps")
    assert len(attempts) >= 1
    completed = [a for a in attempts if a["status"] == "completed"]
    assert len(completed) >= 1
    assert completed[0]["step_name"] == "second"


async def test_postgres_history_step_attempts_are_isolated_by_namespace(postgres_url) -> None:
    ns_a = PostgresRunHistory(
        PostgresRunHistoryConfig(url=postgres_url, schema="test_owf", namespace_id="sa-a")
    )
    ns_b = PostgresRunHistory(
        PostgresRunHistoryConfig(url=postgres_url, schema="test_owf", namespace_id="sa-b")
    )
    await ns_a.connect()
    await ns_b.connect()
    try:
        await ns_a.start("run-sa", "wf", {})
        await ns_a.record_step_attempt("run-sa", "step", status="running")

        a_attempts = await ns_a.list_step_attempts("run-sa")
        b_attempts = await ns_b.list_step_attempts("run-sa")
        assert len(a_attempts) == 1
        assert len(b_attempts) == 0
    finally:
        await ns_a.close()
        await ns_b.close()


async def test_postgres_history_stats_summary(postgres_url) -> None:
    config = PostgresRunHistoryConfig(url=postgres_url, schema="test_owf", namespace_id="stats")
    store = PostgresRunHistory(config)
    await store.connect()
    try:
        await store.start("run-1", "wf-a", {})
        await store.finish("run-1", state={}, output="done")
        await store.start("run-2", "wf-a", {})
        await store.finish("run-2", state={}, error=RuntimeError("boom"))
        await store.start("run-3", "wf-b", {})

        summary = await store.stats_summary()
        assert summary["total"] == 3
        assert summary["by_status"]["completed"] == 1
        assert summary["by_status"]["failed"] == 1
        assert summary["by_status"]["running"] == 1
        assert summary["duration_seconds"]["p50"] is not None

        filtered = await store.stats_summary(workflow="wf-a")
        assert filtered["total"] == 2
        assert filtered["by_status"]["completed"] == 1
    finally:
        await store.close()


async def test_postgres_history_failure_summary(postgres_url) -> None:
    config = PostgresRunHistoryConfig(url=postgres_url, schema="test_owf", namespace_id="failures")
    store = PostgresRunHistory(config)
    await store.connect()
    try:
        await store.start("fail-1", "wf", {})
        await store.finish("fail-1", state={}, error=RuntimeError("first"))
        await store.start("fail-2", "wf", {})
        await store.finish("fail-2", state={}, error=RuntimeError("second"))

        failures = await store.failure_summary()
        assert len(failures) == 2
        assert all(f["error"] is not None for f in failures)
    finally:
        await store.close()
