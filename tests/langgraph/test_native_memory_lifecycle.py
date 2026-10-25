"""Actual-store first-use contention and bounded provider shutdown."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

pytest.importorskip("langgraph.store.base")
from open_workflow_agent.errors import ConfigurationError
from open_workflow_agent.storage import ensure_storage_namespace, namespaced_datasource
from open_workflow_agent_langgraph import native_memory_provider as providers
from open_workflow_agent_langgraph.native_memory_setup import memory_setup_lock
from test_native_memory_provider import provider_services  # noqa: F401


@pytest.fixture
def fresh_provider(provider_services, tmp_path, monkeypatch):  # noqa: F811
    services, backend = provider_services
    if backend == "postgresql":
        import psycopg
        from psycopg import sql

        datasource = services.config.persistence.datasource
        schema = "memory_first_use_" + uuid.uuid4().hex
        ensure_storage_namespace(datasource, schema)
        path = namespaced_datasource(datasource, schema)
    else:
        path = str(tmp_path / "fresh-native.sqlite3")
    monkeypatch.setattr(services, "memory_provider_database_path", lambda *_a, **_kw: path)
    try:
        yield services, backend, path
    finally:
        if backend == "postgresql":
            with psycopg.connect(datasource, autocommit=True) as connection:
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.asyncio
async def test_concurrent_first_use_preserves_all_records_and_reopens(fresh_provider):
    services, _backend, _path = fresh_provider
    start = asyncio.Event()
    namespace = ("first-use", uuid.uuid4().hex)

    async def worker(index):
        await start.wait()
        async with providers.open_native_memory_provider(
            services, provider_id="preferences", version="1.0.0"
        ) as provider:
            await provider.store.aput(namespace, str(index), {"worker": index})

    tasks = [asyncio.create_task(worker(index)) for index in range(3)]
    start.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert not [
        (type(result).__name__, type(result.__context__).__name__)
        for result in results
        if isinstance(result, BaseException)
    ]
    async with providers.open_native_memory_provider(
        services, provider_id="preferences", version="1.0.0"
    ) as reopened:
        for index in range(3):
            item = await reopened.store.aget(namespace, str(index), refresh_ttl=False)
            assert item is not None and item.value == {"worker": index}


@pytest.mark.asyncio
async def test_process_exit_releases_setup_lock_and_preserves_committed_sdk_data(fresh_provider):
    services, backend, path = fresh_provider
    # Development harness only. The child exits while owning the setup lock,
    # without context cleanup, after real SDK migration and data commit.
    worker = r"""
import asyncio,os,sys
from importlib import import_module
from open_workflow_agent_langgraph.native_memory_setup import memory_setup_lock
from open_workflow_agent_langgraph.native_memory_mutations import prepare_mutation_storage
async def main():
    path,backend=sys.argv[1:]
    pg=backend=='postgresql'
    module=import_module('langgraph.store.postgres.aio' if pg else 'langgraph.store.sqlite.aio')
    cls=module.AsyncPostgresStore if pg else module.AsyncSqliteStore
    async with cls.from_conn_string(path) as store:
        async with memory_setup_lock(store,path,postgresql=pg):
            await store.setup()
            await prepare_mutation_storage(store,postgresql=pg)
            await store.aput(('setup-crash',),'committed',{'version':1})
            os._exit(0)
if sys.platform=='win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(main())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", worker, path, backend],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    async with providers.open_native_memory_provider(
        services, provider_id="preferences", version="1.0.0"
    ) as provider:
        item = await provider.store.aget(("setup-crash",), "committed", refresh_ttl=False)
        assert item is not None and item.value == {"version": 1}
        await provider.store.aput(("setup-crash",), "after", {"version": 2})


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
async def test_setup_wait_is_bounded_and_releases_connections(
    fresh_provider, monkeypatch, interruption
):
    services, backend, path = fresh_provider
    module = providers.import_module(
        "langgraph.store.postgres.aio" if backend == "postgresql" else "langgraph.store.sqlite.aio"
    )
    store_type = module.AsyncPostgresStore if backend == "postgresql" else module.AsyncSqliteStore
    waiting = asyncio.Event()

    @asynccontextmanager
    async def observed_lock(store, path, *, postgresql):
        waiting.set()
        async with memory_setup_lock(store, path, postgresql=postgresql):
            yield

    monkeypatch.setattr(providers, "memory_setup_lock", observed_lock)

    async def open_waiter():
        async with providers.open_native_memory_provider(
            services,
            provider_id="preferences",
            version="1.0.0",
            setup_timeout_seconds=0.2 if interruption == "timeout" else 5,
        ):
            pytest.fail("provider entered while setup lock was held")

    async with store_type.from_conn_string(path) as holder:
        async with memory_setup_lock(holder, path, postgresql=backend == "postgresql"):
            task = asyncio.create_task(open_waiter())
            await asyncio.wait_for(waiting.wait(), timeout=5)
            if interruption == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=2)
            else:
                with pytest.raises(ConfigurationError) as error:
                    await asyncio.wait_for(task, timeout=2)
                assert error.value.details == {"reason": "store_setup_failure"}
    # A failed/cancelled contender must leave neither a setup lock nor a broken
    # migration; the next worker initializes and actually reads/writes the store.
    async with providers.open_native_memory_provider(
        services, provider_id="preferences", version="1.0.0"
    ) as provider:
        await provider.store.aput(("recovered",), "key", {"ok": True})
        assert (await provider.store.aget(("recovered",), "key")).value == {"ok": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["close_error", "close_timeout", "caller_error", "caller_cancel"]
)
async def test_actual_connection_closes_and_original_failure_survives(
    fresh_provider, monkeypatch, failure
):
    services, backend, _path = fresh_provider
    module = providers.import_module(
        "langgraph.store.postgres.aio" if backend == "postgresql" else "langgraph.store.sqlite.aio"
    )
    store_type = module.AsyncPostgresStore if backend == "postgresql" else module.AsyncSqliteStore
    closed = asyncio.Event()
    actual = []

    class FaultyShutdown(store_type):
        @classmethod
        @asynccontextmanager
        async def from_conn_string(cls, path):
            async with store_type.from_conn_string(path) as store:
                actual.append(store)
                yield store
            try:
                if failure == "close_timeout":
                    await asyncio.Event().wait()
                raise RuntimeError("secret provider credential")
            finally:
                closed.set()

    monkeypatch.setattr(
        providers,
        "import_module",
        lambda _name: SimpleNamespace(
            AsyncPostgresStore=FaultyShutdown, AsyncSqliteStore=FaultyShutdown
        ),
    )
    original = asyncio.CancelledError() if failure == "caller_cancel" else RuntimeError("caller")
    expected = type(original) if failure.startswith("caller_") else ConfigurationError
    with pytest.raises(expected) as error:
        async with providers.open_native_memory_provider(
            services, provider_id="preferences", version="1.0.0", setup_timeout_seconds=0.5
        ):
            if failure.startswith("caller_"):
                raise original
    assert closed.is_set()
    if failure.startswith("caller_"):
        assert error.value is original
    else:
        assert error.value.details == {"reason": "store_close_failure"}
        assert "secret" not in str(error.value)
    if backend == "postgresql":
        assert actual[0].conn.closed
    else:
        with pytest.raises(ValueError):
            await actual[0].conn.execute("SELECT 1")
