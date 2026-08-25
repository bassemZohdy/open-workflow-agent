"""Common knowledge ingestion and embedded vector search."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import yaml

from .errors import KnowledgeError


class EmbeddingProvider(Protocol):
    dimensions: int
    identity: str

    def embed(self, text: str) -> np.ndarray: ...


class DeterministicEmbeddingProvider:
    """Small offline provider reserved for deterministic tests."""

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions
        self.identity = f"deterministic-sha256/{dimensions}"

    def embed(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimensions, dtype=np.float32)
        for token in re.findall(r"\w+", text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector


class SentenceTransformerEmbeddingProvider:
    """Pinned local CPU embedding provider for mounted-folder knowledge."""

    model_name = "sentence-transformers/all-MiniLM-L6-v2"
    model_revision = "ea78891063587eb050ed4166b20062eaf978037c"

    def __init__(
        self,
        *,
        model_name: str = model_name,
        model_revision: str = model_revision,
        model: Any | None = None,
    ) -> None:
        self.model_name = model_name
        self.model_revision = model_revision
        self.identity = f"{model_name}@{model_revision}"
        self._model = model
        self.dimensions = 384

    def embed(self, text: str) -> np.ndarray:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]  # noqa: I001
            except ImportError as exc:
                raise KnowledgeError(
                    "sentence-transformers is required for the configured local embedding model"
                ) from exc
            self._model = SentenceTransformer(
                self.model_name,
                revision=self.model_revision,
                device="cpu",
                cache_folder=os.getenv("SENTENCE_TRANSFORMERS_HOME"),
            )
            self.dimensions = int(self._model.get_sentence_embedding_dimension())
        vector = self._model.encode(
            [text], convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
        )[0]
        return np.asarray(vector, dtype=np.float32)


# Backward-compatible import for integrations that used the provisional test provider.
LocalEmbeddingProvider = DeterministicEmbeddingProvider


class KnowledgeService:
    def __init__(
        self,
        root: str | Path,
        database: str | Path,
        *,
        embedding: EmbeddingProvider | None = None,
        chunk_size: int = 400,
        chunk_overlap: int = 40,
    ) -> None:
        self.root = Path(root)
        self.database = Path(database)
        self.embedding = embedding or SentenceTransformerEmbeddingProvider()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database, check_same_thread=False)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS manifest (
                path TEXT PRIMARY KEY,
                hash TEXT,
                parser TEXT,
                chunking TEXT,
                embedding TEXT
            )"""
        )
        self.connection.execute("""CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            path TEXT,
            content TEXT,
            vector BLOB
        )""")
        self.connection.commit()
        self._watch_task: asyncio.Task[None] | None = None

    def reload(self) -> dict[str, int]:
        if not self.root.exists():
            return {"added": 0, "updated": 0, "deleted": 0, "unchanged": 0}
        current: dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and path.suffix.lower() in {
                ".txt",
                ".md",
                ".markdown",
                ".json",
                ".yaml",
                ".yml",
                ".pdf",
            }:
                current[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        existing = {
            row[0]: (row[1], row[2])
            for row in self.connection.execute("SELECT path, hash, embedding FROM manifest")
        }
        counts = {"added": 0, "updated": 0, "deleted": 0, "unchanged": 0}
        for path_string, digest in current.items():
            if path_string in existing and existing[path_string] == (
                digest,
                self.embedding.identity,
            ):
                counts["unchanged"] += 1
                continue
            if path_string in existing:
                counts["updated"] += 1
                self.connection.execute("DELETE FROM chunks WHERE path = ?", (path_string,))
            else:
                counts["added"] += 1
            path = Path(path_string)
            text = self._parse(path)
            for chunk in self._chunks(text):
                vector = self.embedding.embed(chunk).astype(np.float32).tobytes()
                self.connection.execute(
                    "INSERT INTO chunks(path, content, vector) VALUES (?, ?, ?)",
                    (path_string, chunk, vector),
                )
            self.connection.execute(
                """INSERT OR REPLACE INTO manifest(
                    path, hash, parser, chunking, embedding
                ) VALUES (?, ?, ?, ?, ?)""",
                (
                    path_string,
                    digest,
                    path.suffix.lower(),
                    str(self.chunk_size),
                    self.embedding.identity,
                ),
            )
        for removed in set(existing) - set(current):
            counts["deleted"] += 1
            self.connection.execute("DELETE FROM chunks WHERE path = ?", (removed,))
            self.connection.execute("DELETE FROM manifest WHERE path = ?", (removed,))
        self.connection.commit()
        return counts

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not self.connection.execute("SELECT 1 FROM chunks LIMIT 1").fetchone():
            return []
        query_vector = self.embedding.embed(query)
        scored: list[dict[str, Any]] = []
        for path, content, blob in self.connection.execute(
            "SELECT path, content, vector FROM chunks"
        ):
            vector = np.frombuffer(blob, dtype=np.float32)
            score = float(np.dot(query_vector, vector))
            scored.append({"path": path, "content": content, "score": score})
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:limit]

    async def start_watch(self, interval_seconds: float = 30.0) -> None:
        if self._watch_task and not self._watch_task.done():
            return

        async def reconcile() -> None:
            while True:
                await asyncio.sleep(max(1.0, interval_seconds))
                await asyncio.to_thread(self.reload)

        self._watch_task = asyncio.create_task(reconcile())

    async def stop_watch(self) -> None:
        if self._watch_task is None:
            return
        self._watch_task.cancel()
        try:
            await self._watch_task
        except asyncio.CancelledError:
            pass
        self._watch_task = None

    def _parse(self, path: Path) -> str:
        try:
            if path.suffix.lower() == ".pdf":
                try:
                    from pypdf import PdfReader

                    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
                except ImportError as exc:
                    raise KnowledgeError(
                        "PDF support requires the optional pypdf dependency"
                    ) from exc
            if path.suffix.lower() in {".json", ".yaml", ".yml"}:
                value = (
                    json.loads(path.read_text(encoding="utf-8"))
                    if path.suffix == ".json"
                    else yaml.safe_load(path.read_text(encoding="utf-8"))
                )
                return json.dumps(value, ensure_ascii=False, indent=2)
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise KnowledgeError(f"unable to parse knowledge file {path}") from exc

    def _chunks(self, text: str) -> list[str]:
        words = text.split()
        if not words:
            return []
        step = max(1, self.chunk_size - self.chunk_overlap)
        return [
            " ".join(words[start : start + self.chunk_size]) for start in range(0, len(words), step)
        ]

    def close(self) -> None:
        self.connection.close()
