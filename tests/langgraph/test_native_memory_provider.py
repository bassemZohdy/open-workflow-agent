"""Installed provider setup is private, optional and separate from checkpoints."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import ConfigurationError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph import native_memory_provider as providers


@pytest.fixture(params=["sqlite", "postgresql"])
def provider_services(request, tmp_path):
    config = RuntimeConfig(model={"provider": "fake"})
    root = tmp_path
    if request.param == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
        root = None
    value = RuntimeServices(config, model=FakeModel(), database_root=root)
    try:
        yield value, request.param
    finally:
        value.close()


@pytest.mark.asyncio
async def test_actual_provider_reopens_separate_store_without_semantic_index_or_ttl(
    provider_services,
):
    services, backend = provider_services
    namespace = ("provider_test", uuid.uuid4().hex)
    async with providers.open_native_memory_provider(
        services, provider_id="preferences", version="1.0.0"
    ) as provider:
        assert provider.binding.backend == backend
        assert provider.reader("preferences")._observations is not None
        assert provider.reader("preferences")._store is provider.store
        await provider.store.aput(namespace, "record", {"color": "雪"})
    async with providers.open_native_memory_provider(
        services, provider_id="preferences", version="1.0.0"
    ) as reopened:
        item = await reopened.store.aget(namespace, "record", refresh_ttl=False)
        assert item is not None and item.value == {"color": "雪"}
        await reopened.store.adelete(namespace, "record")
    path = services.memory_provider_database_path("langgraph", create_namespace=False)
    assert path != services.engine_database_path("langgraph", create_namespace=False)
    if backend == "sqlite":
        assert path.endswith("langgraph-memory.sqlite3")
    else:
        assert "owa_langgraph_memory" in path and "owa_runtime" not in path
    assert services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["disabled", "missing_dependency", "invalid_binding", "event_loop"]
)
async def test_admission_rejects_before_storage_namespace_creation(
    provider_services, monkeypatch, case
):
    services, backend = provider_services
    if case == "event_loop" and backend != "postgresql":
        return
    real_path = services.memory_provider_database_path

    def path(provider, *, create_namespace=True):
        assert not create_namespace, "rejected provider tried to create storage"
        return real_path(provider, create_namespace=False)

    monkeypatch.setattr(services, "memory_provider_database_path", path)
    provider_id = "preferences"
    if case == "disabled":
        services.config.memory.enabled = False
        # RuntimeServices captures the deployment flag at construction.
        services.memory_enabled = False
    elif case == "missing_dependency":

        def missing(_name):
            raise ImportError("credential must not escape")

        monkeypatch.setattr(providers, "import_module", missing)
    elif case == "invalid_binding":
        provider_id = "../provider"
    else:
        monkeypatch.setattr(
            asyncio, "ProactorEventLoop", type(asyncio.get_running_loop()), raising=False
        )
    with pytest.raises((ConfigurationError, ValueError)) as error:
        async with providers.open_native_memory_provider(
            services, provider_id=provider_id, version="1.0.0"
        ):
            pytest.fail("rejected provider was opened")
    assert "credential" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timeout", [True, 0, 31, float("inf"), float("nan"), pytest.param(10**2000, id="huge")]
)
async def test_invalid_setup_bounds_reject_without_opening_storage(services, timeout, monkeypatch):
    def path(*_args, **_kwargs):
        pytest.fail("invalid timeout tried to resolve storage")

    monkeypatch.setattr(services, "memory_provider_database_path", path)
    with pytest.raises(ValueError):
        async with providers.open_native_memory_provider(
            services, provider_id="preferences", version="1.0.0", setup_timeout_seconds=timeout
        ):
            pytest.fail("invalid timeout opened a provider")


@pytest.mark.asyncio
async def test_setup_error_is_sanitized_and_caller_error_keeps_its_identity(services, monkeypatch):
    original = providers.import_module

    class Broken:
        @classmethod
        def from_conn_string(cls, _path):
            raise RuntimeError("secret provider credential")

    class Module:
        AsyncSqliteStore = Broken

    monkeypatch.setattr(providers, "import_module", lambda _name: Module)
    with pytest.raises(ConfigurationError, match="store setup failed") as error:
        async with providers.open_native_memory_provider(
            services, provider_id="preferences", version="1.0.0"
        ):
            pytest.fail("setup failed")
    assert "secret" not in str(error.value)
    monkeypatch.setattr(providers, "import_module", original)
    expected = RuntimeError("caller failure")
    with pytest.raises(RuntimeError) as body:
        async with providers.open_native_memory_provider(
            services, provider_id="preferences", version="1.0.0"
        ):
            raise expected
    assert body.value is expected


@pytest.mark.asyncio
async def test_provider_bound_read_receipt_in_actual_native_workflow(provider_services):
    from open_workflow_agent.execution_authority import authority_scope
    from test_native_memory import AGENT, authority, configuration, invoke, namespace

    services, backend = provider_services
    config = configuration()
    config.persistence.datasource = services.config.persistence.datasource
    runtime = RuntimeServices(config, model=FakeModel(), database_root=services.database_root)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(runtime)
    plan = compile_workflow(generate_default_workflow())
    try:
        async with providers.open_native_memory_provider(
            runtime, provider_id="preferences", version="1.0.0"
        ) as provider:
            reader = provider.reader("preferences")
            await provider.store.aput(
                namespace("alice"), "record", {"backend": backend, "version": 1}
            )

            async def read(_payload, _context):
                first = await reader.get("record")
                await provider.store.aput(namespace("alice"), "record", {"version": 2})
                return {"first": first, "second": await reader.get("record")}

            runtime.catalog.register(AGENT, read)
            with authority_scope(authority(runtime, "alice")):
                result = await invoke(runtime, engine, plan)
            assert result.status == "completed", result.error
            assert result.output == {
                "first": {"backend": backend, "version": 1},
                "second": {"backend": backend, "version": 1},
            }
            rows = runtime.effect_journal.connection.execute(
                "SELECT identity FROM memory_read_observations WHERE invocation_id=?",
                (result.invocation_id,),
            ).fetchall()
            assert len(rows) == 1
            runtime.effect_journal.verify_invocation(result.invocation_id, plan.fingerprint)
            await provider.store.adelete(namespace("alice"), "record")
    finally:
        await engine.shutdown()
        runtime.close()
