"""Offline local embeddings, bounded vectors and SDK transaction-safe reuse."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from importlib import import_module, metadata
from numbers import Real
from pathlib import Path
from typing import Any

from langchain_core.embeddings import Embeddings
from open_workflow_agent.errors import ConfigurationError, ToolError
from open_workflow_agent.memory_semantic import DIMENSIONS, MODEL, MemorySemanticIndex


def artifact_digest(cache: Path) -> str:
    repository = cache / "models--qdrant--all-MiniLM-L6-v2-onnx"
    reference = repository / "refs" / "main"
    if reference.stat().st_size > 128:
        raise ValueError("invalid local model reference")
    revision = reference.read_text().strip()
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("invalid local model revision")
    snapshot = repository / "snapshots" / revision
    files = []
    for count, item in enumerate(snapshot.rglob("*")):
        if count >= 256:
            raise ValueError("local model directory exceeds its bound")
        if item.is_file():
            files.append(item)
            if len(files) > 32:
                raise ValueError("local model file count is invalid")
    files.sort()
    if not 1 <= len(files) <= 32:
        raise ValueError("local model file count is invalid")
    digest = hashlib.sha256()
    for name in ("fastembed", "onnxruntime", "numpy", "tokenizers"):
        digest.update((name + "=" + metadata.version(name) + "\n").encode())
    size = 0
    for item in files:
        target = item.resolve(strict=True)
        target.relative_to(repository.resolve(strict=True))
        file_size = target.stat().st_size
        size += file_size
        if size > 536_870_912:
            raise ValueError("local model artifacts exceed their bound")
        digest.update(item.relative_to(snapshot).as_posix().encode())
        digest.update(b"\0")
        file_digest = hashlib.sha256()
        with target.open("rb") as stream:
            read = 0
            for block in iter(lambda: stream.read(1_048_576), b""):
                read += len(block)
                if read > file_size:
                    raise ValueError("local model changed or exceeded its size bound")
                file_digest.update(block)
            if read != file_size:
                raise ValueError("local model changed during fingerprinting")
        digest.update(file_digest.digest())
    return digest.hexdigest()


def local_embedding_provider() -> Any:
    from open_workflow_agent.knowledge import FastEmbedEmbeddingProvider

    cache = Path(os.getenv("FASTEMBED_CACHE_PATH", "/opt/models"))
    before = artifact_digest(cache)
    model = import_module("fastembed").TextEmbedding(
        model_name=MODEL,
        cache_dir=str(cache),
        threads=1,
        providers=["CPUExecutionProvider"],
        local_files_only=True,
    )
    provider = FastEmbedEmbeddingProvider(cache_dir=cache, model=model)
    provider.embed("native memory model admission")
    if artifact_digest(cache) != before:
        raise ValueError("local model changed during admission")
    provider.artifact_sha256 = before  # type: ignore[attr-defined]
    return provider


def finite_vector(value: Any) -> tuple[float, ...]:
    try:
        values = list(value)
        if len(values) != DIMENSIONS or any(
            not isinstance(v, Real) or isinstance(v, bool) for v in values
        ):
            raise ValueError("invalid dimensions")
        vector = tuple(float(v) for v in values)
        if any(not math.isfinite(v) or abs(v) > 1_000_000 for v in vector):
            raise ValueError("invalid component")
        norm = math.sqrt(math.fsum(v * v for v in vector))
        if norm < 1e-12:
            raise ValueError("zero embedding")
        return tuple(v / norm for v in vector)
    except Exception:
        raise ToolError("local memory embedding is invalid") from None


async def _local_work(operation: Callable[[], Any]) -> Any:
    worker = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(worker)
    except BaseException:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None:
                    current.uncancel()
            except Exception:
                break
        raise


class NativeMemoryEmbeddings(Embeddings):  # type: ignore[misc]
    def __init__(self, provider: Any, policy: MemorySemanticIndex) -> None:
        self.provider = provider
        self.policy = policy
        self._slots = asyncio.Semaphore(1)
        self._prepared: ContextVar[tuple[str, tuple[float, ...]] | None] = ContextVar(
            "memory_embedding", default=None
        )

    @classmethod
    async def open(cls, settings: MemorySemanticIndex) -> NativeMemoryEmbeddings:
        try:
            async with asyncio.timeout(settings.timeout_seconds):
                provider = await _local_work(local_embedding_provider)
                digest = provider.artifact_sha256
                policy = replace(settings, artifact_sha256=digest)
                if settings.artifact_sha256 and settings.artifact_sha256 != digest:
                    raise ValueError("local model differs from deployment pin")
                result = cls(provider, policy)
                await result.aembed_documents(["x"])
                return result
        except Exception:
            raise ConfigurationError(
                "semantic memory requires its installed offline model artifacts"
            ) from None

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        prepared = self._prepared.get()
        if prepared is not None:
            if texts != [prepared[0]]:
                raise ToolError("native SDK requested an unexpected query embedding")
            return [list(prepared[1])]
        if len(texts) != 1:
            raise ToolError("semantic memory requires a bounded single-field embedding")
        text = self.policy.validate_text(texts[0])
        async with asyncio.timeout(self.policy.timeout_seconds):
            async with self._slots:
                # CPU inference cannot be killed by cancelling an await. Keep
                # its slot until the native worker actually drains.
                value = await _local_work(lambda: self.provider.embed(text))
        return [list(finite_vector(value))]

    @contextmanager
    def prepared(self, text: str, vector: Sequence[float]) -> Iterator[None]:
        token = self._prepared.set((text, tuple(vector)))
        try:
            yield
        finally:
            self._prepared.reset(token)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise ToolError("semantic memory requires asynchronous local inference")

    def embed_query(self, text: str) -> list[float]:
        raise ToolError("semantic memory requires asynchronous local inference")

    def index_config(self, *, postgresql: bool) -> dict[str, Any]:
        return index_config(self.policy, self, postgresql=postgresql)

    def rebind(self, settings: MemorySemanticIndex) -> NativeMemoryEmbeddings:
        if settings.artifact_sha256 and settings.artifact_sha256 != self.policy.artifact_sha256:
            raise ConfigurationError("semantic memory differs from its installed artifact pin")
        result = NativeMemoryEmbeddings(
            self.provider, replace(settings, artifact_sha256=self.policy.artifact_sha256)
        )
        result._slots = self._slots
        return result


class FrozenMemoryEmbedding(Embeddings):  # type: ignore[misc]
    def __init__(self, text: str, vector: Sequence[float]) -> None:
        self.text, self.vector = text, tuple(vector)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if texts != [self.text] or len(self.vector) != DIMENSIONS:
            raise ToolError("native SDK requested an unexpected embedding")
        return [list(self.vector)]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def index_config(
    policy: MemorySemanticIndex, embeddings: Embeddings, *, postgresql: bool
) -> dict[str, Any]:
    return {
        "dims": DIMENSIONS,
        "embed": embeddings,
        "fields" if postgresql else "text_fields": [policy.field],
        "distance_type": "cosine",
        **({"ann_index_config": {"kind": "flat", "vector_type": "vector"}} if postgresql else {}),
    }
