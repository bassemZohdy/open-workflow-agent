"""Supplement pinned SDK search SQL with the exact public memory boundary."""

from __future__ import annotations

import json
from typing import Any

from open_workflow_agent.errors import ToolError
from open_workflow_agent.memory_search import MemorySearchQuery


def search_operations(operations: Any) -> Any:
    # The SDK SQLite query builder logs filter parameters at DEBUG. Supply no
    # user filter to that builder; add parameterized predicates afterwards.
    for _, operation in operations:
        if operation.query is not None or operation.offset != 0 or operation.refresh_ttl:
            raise ToolError("native memory search mode is unsupported")
        MemorySearchQuery.create(operation.filter, operation.limit)
    return [(index, operation._replace(filter=None)) for index, operation in operations]


def scoped_queries(prepared: Any, operations: Any, *, postgresql: bool) -> Any:
    from importlib import import_module

    module = import_module(
        "langgraph.store.postgres.base" if postgresql else "langgraph.store.sqlite.base"
    )
    order = " ORDER BY store.updated_at DESC" if postgresql else " ORDER BY updated_at DESC"
    result = []
    for entry, (_, operation) in zip(prepared, operations, strict=True):
        query, parameters = entry[:2]
        filters = MemorySearchQuery.create(operation.filter, operation.limit).filter
        extra, values = [], []
        if not postgresql:
            extra.append("(expires_at IS NULL OR julianday(expires_at)>julianday('now'))")
        namespace = operation.namespace_prefix
        if len(namespace) == 3 and namespace[0] == "owa_native_memory_v1":
            extra.append("store.prefix=%s" if postgresql else "prefix=?")
            values.append(module._namespace_to_text(namespace))
        for key, value in filters.items():
            if postgresql:
                extra.append("store.value->%s=%s::jsonb")
                values.extend([key, json.dumps(value, ensure_ascii=False, allow_nan=False)])
            else:
                path = "$." + key
                if value is None:
                    extra.append("json_type(value,?)='null'")
                    values.append(path)
                else:
                    kind = (
                        ("true" if value else "false")
                        if type(value) is bool
                        else ("text" if type(value) is str else None)
                    )
                    extra.append(
                        "(json_type(value,?) IN ('integer','real') AND json_extract(value,?)=?)"
                        if kind is None
                        else f"(json_type(value,?)='{kind}' AND json_extract(value,?)=?)"
                    )
                    values.extend([path, path, value])
        if query.count(order) != 1:
            raise ToolError("installed native memory search SQL is incompatible")
        replacement = (" AND " + " AND ".join(extra)) if extra else ""
        replacement += " ORDER BY store.key ASC" if postgresql else " ORDER BY key ASC"
        query = query.replace(order, replacement)
        parameters = [*parameters[:-2], *values, *parameters[-2:]]
        result.append((query, parameters) if postgresql else (query, parameters, False))
    return result
