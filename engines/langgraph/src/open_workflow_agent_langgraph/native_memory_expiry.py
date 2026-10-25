"""Pinned SDK get adapters omit expired rows before deserialization."""

from __future__ import annotations

from typing import Any


def expiry_filtered_store(base: Any, *, postgresql: bool) -> Any:
    if postgresql:

        class ExpiryFilteredPostgresStore(base):  # type: ignore[misc,valid-type]
            @property
            def _omit_expired(self) -> bool:
                return True

        return ExpiryFilteredPostgresStore

    class ExpiryFilteredSqliteStore(base):  # type: ignore[misc,valid-type]
        def _get_batch_GET_ops_queries(self, operations: Any) -> Any:
            prepared = super()._get_batch_GET_ops_queries(operations)
            return [
                query._replace(
                    query=query.query
                    + " AND (expires_at IS NULL OR julianday(expires_at)>julianday('now'))"
                )
                if query.kind == "get"
                else query
                for query in prepared
            ]

    return ExpiryFilteredSqliteStore
