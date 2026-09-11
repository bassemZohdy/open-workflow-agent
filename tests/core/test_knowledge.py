from __future__ import annotations

import importlib
from pathlib import Path

import open_workflow_agent.knowledge as knowledge_module
import pytest
from open_workflow_agent.errors import KnowledgeError
from open_workflow_agent.knowledge import (
    DeterministicEmbeddingProvider,
    FastEmbedEmbeddingProvider,
    KnowledgeService,
)


def test_embedding_providers_cover_deterministic_and_injected_models(monkeypatch):
    deterministic = DeterministicEmbeddingProvider(8)
    vector = deterministic.embed("same text")
    assert vector.shape == (8,)
    assert vector.dtype.name == "float32"
    assert deterministic.embed("   ").sum() == 0

    class EmbedModel:
        def embed(self, values):
            assert values == ["hello"]
            return iter([[1.0, 2.0, 3.0]])

    provider = FastEmbedEmbeddingProvider(model=EmbedModel())
    assert provider.embed("hello").tolist() == [1.0, 2.0, 3.0]
    assert provider.dimensions == 3

    class EncodeModel:
        def encode(self, values, **kwargs):
            assert values == ["hello"]
            assert kwargs["normalize_embeddings"] is True
            return [[4.0, 5.0]]

    assert FastEmbedEmbeddingProvider(model=EncodeModel()).embed("hello").tolist() == [4.0, 5.0]
    with pytest.raises(KnowledgeError, match="does not expose"):
        FastEmbedEmbeddingProvider(model=object()).embed("hello")

    def missing_fastembed(_name):
        raise ImportError("missing")

    monkeypatch.setattr(importlib, "import_module", missing_fastembed)
    with pytest.raises(KnowledgeError, match="fastembed is required"):
        FastEmbedEmbeddingProvider().embed("hello")

    class FastEmbedModule:
        class TextEmbedding:
            def __init__(self, **options):
                assert options["local_files_only"] is True
                assert options["cache_dir"] == "cache"

            def embed(self, values):
                return iter([[6.0, 7.0]])

    monkeypatch.setattr(importlib, "import_module", lambda _name: FastEmbedModule)
    lazy = FastEmbedEmbeddingProvider(cache_dir="cache")
    assert lazy.embed("hello").tolist() == [6.0, 7.0]


def test_fastembed_provider_can_be_constructed_without_optional_numpy(monkeypatch):
    monkeypatch.setattr(knowledge_module, "NUMPY_AVAILABLE", False)
    provider = FastEmbedEmbeddingProvider()
    assert provider.identity.endswith("@ea78891063587eb050ed4166b20062eaf978037c")
    with pytest.raises(KnowledgeError, match="numpy is required"):
        provider.embed("hello")


def test_knowledge_service_indexes_supported_files_and_watches_changes(tmp_path: Path):
    root = tmp_path / "knowledge"
    database = tmp_path / "knowledge.sqlite3"
    service = KnowledgeService(
        root,
        database,
        embedding=DeterministicEmbeddingProvider(8),
        chunk_size=2,
        chunk_overlap=1,
    )
    try:
        assert service.reload() == {"added": 0, "updated": 0, "deleted": 0, "unchanged": 0}
        assert service.search("empty") == []
        root.mkdir()
        (root / "notes.txt").write_text("alpha beta gamma", encoding="utf-8")
        (root / "data.json").write_text('{"topic": "json"}', encoding="utf-8")
        (root / "config.yaml").write_text("topic: yaml\n", encoding="utf-8")
        (root / "ignored.bin").write_bytes(b"ignored")
        assert service.reload() == {"added": 3, "updated": 0, "deleted": 0, "unchanged": 0}
        assert service.reload() == {"added": 0, "updated": 0, "deleted": 0, "unchanged": 3}
        assert service.search("alpha", limit=1)[0]["path"].endswith("notes.txt")
        assert service.search("missing") == [] or service.search("missing")[0]["score"] <= 1
        (root / "notes.txt").write_text("changed text", encoding="utf-8")
        (root / "config.yaml").unlink()
        assert service.reload() == {"added": 0, "updated": 1, "deleted": 1, "unchanged": 1}
        assert service._chunks("") == []
        assert service._chunks("one two three") == ["one two", "two three", "three"]
        (root / "config.yaml").write_text("topic: yaml\n", encoding="utf-8")
        assert KnowledgeService._parser_identity(Path("note.txt")) == "text"
        assert KnowledgeService._parser_identity(Path("data.json")) == "stdlib-json"
        assert KnowledgeService._parser_identity(Path("config.yaml")).startswith("pyyaml@")
        assert KnowledgeService._parser_identity(Path("document.pdf")).startswith("pypdf@")
        assert service._parse(root / "data.json").strip().startswith("{")
        assert "topic" in service._parse(root / "config.yaml")
        with pytest.raises(KnowledgeError, match="unable to parse"):
            service._parse(root / "missing.txt")

        assert service.reload() == {"added": 1, "updated": 0, "deleted": 0, "unchanged": 2}
        service.chunk_size = 1
        service.chunk_overlap = 0
        assert service.reload() == {"added": 0, "updated": 3, "deleted": 0, "unchanged": 0}
        with pytest.raises(KnowledgeError, match="limit must be between"):
            service.search("alpha", limit=101)
    finally:
        service.close()


@pytest.mark.asyncio
async def test_knowledge_watch_task_is_idempotent_and_stoppable(tmp_path: Path):
    root = tmp_path / "knowledge"
    root.mkdir()
    service = KnowledgeService(
        root,
        tmp_path / "knowledge.sqlite3",
        embedding=DeterministicEmbeddingProvider(4),
    )
    await service.start_watch(interval_seconds=0.01)
    first = service._watch_task
    await service.start_watch(interval_seconds=0.01)
    assert service._watch_task is first
    await service.stop_watch()
    assert service._watch_task is None
    await service.stop_watch()
    service.close()
