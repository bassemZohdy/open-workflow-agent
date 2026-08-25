from __future__ import annotations

from open_workflow_agent.knowledge import (
    DeterministicEmbeddingProvider,
    KnowledgeService,
    SentenceTransformerEmbeddingProvider,
)
from open_workflow_agent.memory import MemoryService
from open_workflow_agent.persistence import InvocationStore


def test_memory_add_search_delete(tmp_path):
    memory = MemoryService(str(tmp_path / "memory.sqlite3"))
    identifier = memory.add("license renewal policy", {"source": "policy"})
    assert memory.search("renewal")[0]["id"] == identifier
    memory.delete(identifier)
    assert memory.search("renewal") == []
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
    import asyncio

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
    class FakeSentenceTransformer:
        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, values, **kwargs):
            assert values == ["hello"]
            return [[1.0, 0.0, 0.0]]

    provider = SentenceTransformerEmbeddingProvider(model=FakeSentenceTransformer())
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
    assert services.agent_tools == ("search_knowledge", "catalog")
    services.close()
