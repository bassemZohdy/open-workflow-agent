"""Offline model identity, finite vectors and cancellation drain behavior."""

import asyncio
import threading
from pathlib import Path

import pytest
from open_workflow_agent.errors import ToolError
from open_workflow_agent.memory_semantic import MemorySemanticIndex

pytest.importorskip("langgraph.store.base")
from open_workflow_agent_langgraph import native_memory_embeddings as module


@pytest.mark.parametrize(
    "vector",
    [[], [1.0], [0.0] * 384, [float("nan")] * 384, [float("inf")] * 384, ["1"] * 384, [True] * 384],
)
def test_embedding_rejects_invalid_dimensions_values_and_zero_norm(vector):
    with pytest.raises(ToolError):
        module.finite_vector(vector)


@pytest.mark.asyncio
async def test_repeated_cancellation_does_not_release_inference_slot_before_worker_finishes():
    started, release, second_started = threading.Event(), threading.Event(), threading.Event()

    class BlockedModel:
        def embed(self, text):
            if text == "coffee":
                started.set()
                assert release.wait(5)
            else:
                second_started.set()
            return [1.0] + [0.0] * 383

    model = module.NativeMemoryEmbeddings(
        BlockedModel(), MemorySemanticIndex(artifact_sha256="a" * 64)
    )
    first = asyncio.create_task(model.aembed_documents(["coffee"]))
    second = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()
        second = asyncio.create_task(model.aembed_documents(["tea"]))
        await asyncio.sleep(0.02)
        assert not first.done() and not second_started.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        if second is not None:
            assert len((await second)[0]) == 384


@pytest.mark.asyncio
async def test_admission_cancellation_drains_model_loader(monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def factory():
        started.set()
        assert release.wait(5)
        finished.set()
        return None

    monkeypatch.setattr(module, "local_embedding_provider", factory)
    pending = asyncio.create_task(module.NativeMemoryEmbeddings.open(MemorySemanticIndex()))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0.02)
        assert not pending.done() and not finished.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert finished.is_set()


def cache(tmp_path: Path):
    repository = tmp_path / "models--qdrant--all-MiniLM-L6-v2-onnx"
    (repository / "refs").mkdir(parents=True)
    (repository / "refs/main").write_text("a" * 40)
    snapshot = repository / "snapshots" / ("a" * 40)
    snapshot.mkdir(parents=True)
    (snapshot / "model.onnx").write_bytes(b"controlled model artifact")
    return snapshot


def test_model_fingerprint_binds_actual_bytes_and_inference_versions(tmp_path, monkeypatch):
    snapshot = cache(tmp_path)
    monkeypatch.setattr(module.metadata, "version", lambda _name: "fixture-1")
    original = module.artifact_digest(tmp_path)
    (snapshot / "model.onnx").write_bytes(b"changed model artifact")
    assert module.artifact_digest(tmp_path) != original
    (snapshot / "model.onnx").write_bytes(b"controlled model artifact")
    monkeypatch.setattr(module.metadata, "version", lambda _name: "fixture-2")
    assert module.artifact_digest(tmp_path) != original


def test_model_fingerprint_rejects_unbounded_cache_layout(tmp_path, monkeypatch):
    snapshot = cache(tmp_path)
    monkeypatch.setattr(module.metadata, "version", lambda _name: "fixture-1")
    for number in range(33):
        (snapshot / str(number)).write_text("fixture")
    with pytest.raises(ValueError):
        module.artifact_digest(tmp_path)
