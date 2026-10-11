"""In-process workflow registry used by nested subflow execution."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

from openworkflow_adk.models import OpenWorkflowDocument


@dataclass(frozen=True)
class WorkflowSearchResult:
    """A ranked workflow discovery result."""

    document: OpenWorkflowDocument
    score: float
    matched_terms: tuple[str, ...]


class WorkflowRegistry:
    """Resolve workflow references by namespace, name, and version."""

    def __init__(self, documents: Iterable[OpenWorkflowDocument] = ()) -> None:
        self._documents: dict[tuple[str, str, str], OpenWorkflowDocument] = {}
        for document in documents:
            self.register(document)

    def register(self, document: OpenWorkflowDocument) -> None:
        key = (document.document.namespace, document.document.name, document.document.version)
        if key in self._documents:
            raise ValueError(f"workflow already registered: {key!r}")
        self._documents[key] = document

    def documents(self) -> list[OpenWorkflowDocument]:
        """Return registered documents in stable identity order."""
        return sorted(
            self._documents.values(),
            key=lambda item: (
                item.document.namespace,
                item.document.name,
                item.document.version,
            ),
        )

    def resolve(self, namespace: str, name: str, version: str = "latest") -> OpenWorkflowDocument:
        if version != "latest":
            key = (namespace, name, version)
            if key not in self._documents:
                raise KeyError(f"workflow not found: {key!r}")
            return self._documents[key]
        candidates = [
            document
            for (item_namespace, item_name, _), document in self._documents.items()
            if item_namespace == namespace and item_name == name
        ]
        if not candidates:
            raise KeyError(f"workflow not found: {(namespace, name, version)!r}")
        return max(candidates, key=lambda item: _version_key(item.document.version))

    def search(self, query: str, *, limit: int = 10) -> list[WorkflowSearchResult]:
        """Find workflows whose metadata or task intent matches ``query``.

        This lexical baseline is deterministic and provides the same result
        shape an optional embedding index can later implement.
        """
        if limit < 1:
            raise ValueError("limit must be positive")
        terms = {term for term in re.findall(r"[a-z0-9]+", query.casefold()) if len(term) > 1}
        if not terms:
            return []
        results: list[WorkflowSearchResult] = []
        for document in self._documents.values():
            text_parts = [
                document.document.namespace,
                document.document.name,
                document.document.title or "",
                document.document.summary or "",
                " ".join(str(value) for value in document.document.tags.values()),
            ]
            for item in document.do:
                text_parts.append(item.name)
                agent_config = item.task.effective_agent()
                if agent_config:
                    text_parts.extend(
                        [agent_config.instruction or "", agent_config.description or ""]
                    )
            haystack = " ".join(text_parts).casefold()
            matched = tuple(sorted(term for term in terms if term in haystack))
            if matched:
                results.append(
                    WorkflowSearchResult(
                        document=document,
                        score=len(matched) / len(terms),
                        matched_terms=matched,
                    )
                )
        return sorted(results, key=lambda result: (-result.score, result.document.document.name))[
            :limit
        ]


def _version_key(version: str) -> tuple[int, Version | str]:
    """Return a deterministic PEP 440 ordering key with a legacy fallback."""
    try:
        return (1, Version(version))
    except InvalidVersion:
        return (0, version)
