from __future__ import annotations

import asyncio

import pytest
from open_workflow_agent.catalog import CatalogContext, FakeModel, ManagedFunctionCapabilities
from open_workflow_agent.errors import ConfigurationError, ToolError
from open_workflow_agent.knowledge import (
    DeterministicEmbeddingProvider,
    KnowledgeService,
    SentenceTransformerEmbeddingProvider,
)
from open_workflow_agent.memory import MemoryService
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.workflow import compile_workflow


def test_memory_add_search_delete(tmp_path):
    memory = MemoryService(str(tmp_path / "memory.sqlite3"))
    identifier = memory.add("license renewal policy", {"source": "policy"})
    assert memory.search("renewal")[0]["id"] == identifier
    assert memory.delete(identifier) is True
    assert memory.search("renewal") == []
    memory.close()


def test_memory_without_database_has_stable_ids_and_delete_result():
    memory = MemoryService()
    first = memory.add("first")
    second = memory.add("second")
    assert [item["id"] for item in memory.search("")] == [first, second]
    assert memory.delete(first) is True
    assert memory.delete(first) is False
    assert [item["id"] for item in memory.search("")] == [second]
    memory.close()


def test_knowledge_manifest_skips_unchanged_documents(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "policy.md").write_text("License renewal requires an application.", encoding="utf-8")
    knowledge = KnowledgeService(
        root, tmp_path / "knowledge.sqlite3", embedding=DeterministicEmbeddingProvider()
    )
    assert knowledge.reload()["added"] == 1
    assert knowledge.reload()["unchanged"] == 1
    assert knowledge.search("renewal")[0]["path"].endswith("policy.md")
    (root / "policy.md").unlink()
    assert knowledge.reload()["deleted"] == 1
    knowledge.close()


def test_knowledge_reindexes_when_embedding_identity_changes(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "policy.md").write_text("License renewal policy.", encoding="utf-8")
    database = tmp_path / "knowledge.sqlite3"
    first = KnowledgeService(root, database, embedding=DeterministicEmbeddingProvider(8))
    assert first.reload()["added"] == 1
    first.close()
    second = KnowledgeService(root, database, embedding=DeterministicEmbeddingProvider(16))
    assert second.reload()["updated"] == 1
    second.close()


def test_knowledge_watch_can_start_and_stop(tmp_path):
    async def run():
        knowledge = KnowledgeService(
            tmp_path / "knowledge",
            tmp_path / "knowledge.sqlite3",
            embedding=DeterministicEmbeddingProvider(),
        )
        await knowledge.start_watch(1)
        await knowledge.stop_watch()
        knowledge.close()

    asyncio.run(run())


def test_pinned_embedding_provider_is_injectable_and_records_identity():
    class FakeFastEmbed:
        def embed(self, values):
            assert values == ["hello"]
            yield [1.0, 0.0, 0.0]

    provider = SentenceTransformerEmbeddingProvider(model=FakeFastEmbed())
    assert provider.identity.endswith("@ea78891063587eb050ed4166b20062eaf978037c")
    assert provider.embed("hello").tolist() == [1.0, 0.0, 0.0]


def test_invocation_fingerprint_is_persisted_and_checked(tmp_path):
    store = InvocationStore(tmp_path / "runtime.sqlite3")
    handle = store.create(
        engine="test",
        session_id="s1",
        user_id="u1",
        workflow_name="w",
        workflow_version="1",
        workflow_fingerprint="abc",
    )
    assert store.get(handle.invocation_id).session_id == "s1"
    store.verify_fingerprint(handle, "abc")
    store.close()


def test_tools_are_external_configuration_not_workflow_state(tmp_path):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    config = RuntimeConfig.model_validate(
        {"tools": [{"type": "mcp", "name": "catalog", "endpoint": "http://localhost:9000"}]}
    )
    services = RuntimeServices(config, database_root=tmp_path)
    assert services.tools.names() == ("catalog",)
    assert services.agent_tools == (
        "search_knowledge",
        "add_memory",
        "search_memory",
        "delete_memory",
        "catalog",
    )
    services.close()


def test_managed_catalog_context_scopes_runtime_services(tmp_path):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        context = CatalogContext(model=services.model, services=services)
        assert isinstance(context.services, ManagedFunctionCapabilities)
        assert context.services.agent_tools == services.agent_tools
        assert not hasattr(context.services, "config")
        assert not hasattr(context.services, "sandbox")
        assert not hasattr(context.services, "memory")
        assert (
            asyncio.run(context.services.invoke_agent_tool("search_knowledge", {"query": ""})) == []
        )
        with pytest.raises(ToolError, match="not approved"):
            asyncio.run(context.services.invoke_agent_tool("arbitrary-subprocess", {}))
    finally:
        services.close()


def test_configured_sqlite_datasource_is_shared_by_durable_services(tmp_path):
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    database = tmp_path / "runtime.sqlite3"
    config = RuntimeConfig.model_validate(
        {"persistence": {"datasource": f"sqlite:///{database.as_posix()}"}}
    )
    services = RuntimeServices(config, model=FakeModel())
    identifier = services.memory.add("durable datasource memory")
    plan = compile_workflow()
    handle = services.invocations.create(
        engine="test",
        session_id="session",
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    assert services.memory.search("durable")[0]["id"] == identifier
    assert services.invocations.get(handle.invocation_id) is not None
    services.close()

    reopened = RuntimeServices(config, model=FakeModel())
    assert reopened.memory.search("durable")[0]["id"] == identifier
    assert reopened.invocations.get(handle.invocation_id) is not None
    assert reopened.datasource == str(database)
    reopened.close()


def test_unsupported_datasource_is_not_silently_treated_as_sqlite():
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices

    config = RuntimeConfig.model_validate(
        {"persistence": {"datasource": "postgresql://example.invalid/owa"}}
    )
    try:
        RuntimeServices(config)
    except ConfigurationError as exc:
        assert exc.details["scheme"] == "postgresql"
    else:
        raise AssertionError("unsupported datasource was accepted")


def test_sqlite_absolute_url_normalizes_posix_root():
    import os

    if os.name == "nt":
        return
    from open_workflow_agent.storage import resolve_datasource

    assert resolve_datasource("sqlite:////tmp/owa-runtime.sqlite3") == "/tmp/owa-runtime.sqlite3"
