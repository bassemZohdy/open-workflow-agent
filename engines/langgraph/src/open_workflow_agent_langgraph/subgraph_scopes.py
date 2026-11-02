"""Bounded reference traversal for native subgraph admission and projection."""

from __future__ import annotations

from collections.abc import Iterator


def ancestor_references(reference: str) -> Iterator[str]:
    """Yield strict path ancestors, nearest first, without scanning unrelated scopes."""
    end = reference.rfind("/")
    while end > 0:
        yield reference[:end]
        end = reference.rfind("/", 0, end)
