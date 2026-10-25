"""Actual native checkpoints initialize safely on an empty shared database."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from open_workflow_agent.storage import ensure_storage_namespace, namespaced_datasource

pytest.importorskip("langgraph.checkpoint.base")
from open_workflow_agent_langgraph.native import LangGraphFunctionalAdapter


@pytest.fixture(params=["sqlite", "postgresql"])
def checkpoint_database(request, tmp_path):
    if request.param == "sqlite":
        yield str(tmp_path / "checkpoints.sqlite3"), request.param
        return
    datasource = os.getenv("OWA_TEST_POSTGRES_URL")
    if not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    import psycopg
    from psycopg import sql

    schema = "checkpoint_setup_" + uuid4().hex
    ensure_storage_namespace(datasource, schema)
    try:
        yield namespaced_datasource(datasource, schema), request.param
    finally:
        with psycopg.connect(datasource, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.asyncio
async def test_concurrent_checkpoint_first_use_keeps_independent_results(checkpoint_database):
    path, _backend = checkpoint_database
    start = asyncio.Event()

    async def worker(index):
        adapter = LangGraphFunctionalAdapter()
        adapter.database_path = path
        await start.wait()

        async def run(value):
            return {"seen": value}

        return await adapter.invoke(run, {"worker": index}, thread_id=uuid4().hex)

    tasks = [asyncio.create_task(worker(index)) for index in range(3)]
    start.set()
    assert await asyncio.gather(*tasks) == [{"seen": {"worker": index}} for index in range(3)]
