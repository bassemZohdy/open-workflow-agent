"""Supplement pinned SDK search SQL with the exact public memory boundary."""

from __future__ import annotations

import json
import re
from typing import Any

from open_workflow_agent.errors import ToolError
from open_workflow_agent.memory_search import MemorySearchQuery


def search_operations(operations: Any) -> Any:
    # The SDK SQLite query builder logs filter parameters at DEBUG. Supply no
    # user filter to that builder; add parameterized predicates afterwards.
    for _, operation in operations:
        if operation.offset != 0 or operation.refresh_ttl:
            raise ToolError("native memory search mode is unsupported")
        if operation.query is not None and (
            type(operation.query) is not str
            or not operation.query.strip()
            or len(operation.query.encode()) > 16_384
        ):
            raise ToolError("native memory semantic query is invalid")
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
        semantic = operation.query is not None
        alias = "store." if postgresql else "s." if semantic else ""
        extra, values = [], []
        if not postgresql:
            extra.append(
                f"({alias}expires_at IS NULL OR julianday({alias}expires_at)>julianday('now'))"
            )
        namespace = operation.namespace_prefix
        if len(namespace) == 3 and namespace[0] == "owa_native_memory_v1":
            extra.append(f"{alias}prefix=" + ("%s" if postgresql else "?"))
            values.append(module._namespace_to_text(namespace))
        for key, value in filters.items():
            if postgresql:
                extra.append("store.value->%s=%s::jsonb")
                values.extend([key, json.dumps(value, ensure_ascii=False, allow_nan=False)])
            else:
                path = "$." + key
                if value is None:
                    extra.append(f"json_type({alias}value,?)='null'")
                    values.append(path)
                else:
                    kind = (
                        ("true" if value else "false")
                        if type(value) is bool
                        else ("text" if type(value) is str else None)
                    )
                    extra.append(
                        f"(json_type({alias}value,?) IN ('integer','real') "
                        f"AND json_extract({alias}value,?)=?)"
                        if kind is None
                        else (
                            f"(json_type({alias}value,?)='{kind}' "
                            f"AND json_extract({alias}value,?)=?)"
                        )
                    )
                    values.extend([path, path, value])
        if semantic:
            if "JOIN store_vectors sv" not in query:
                raise ToolError("installed native semantic index is unavailable")
            position = query.index("ORDER BY", query.index("JOIN store_vectors sv"))
            query = query[:position] + " AND " + " AND ".join(extra) + " " + query[position:]
            if postgresql:
                query, changes = re.subn(
                    r"ORDER BY ([^\n]+?) ASC(\s+LIMIT %s)",
                    r"ORDER BY (\1)+0 ASC, store.key ASC\2",
                    query,
                    count=1,
                )
                if changes != 1:
                    raise ToolError("installed native semantic SQL is incompatible")
                query = query.replace("ORDER BY score DESC", "ORDER BY score DESC, key ASC")
            else:
                query = query.replace("ORDER BY score DESC", "ORDER BY score DESC, s.key ASC", 1)
                query = query.replace("ORDER BY score DESC\n", "ORDER BY score DESC, key ASC\n")
            tail = 4 if postgresql else 3
            parameters = [*parameters[:-tail], *values, *parameters[-tail:]]
            result.append((query, parameters) if postgresql else (query, parameters, False))
            continue
        if query.count(order) != 1:
            raise ToolError("installed native memory search SQL is incompatible")
        replacement = (" AND " + " AND ".join(extra)) if extra else ""
        replacement += " ORDER BY store.key ASC" if postgresql else " ORDER BY key ASC"
        query = query.replace(order, replacement)
        parameters = [*parameters[:-2], *values, *parameters[-2:]]
        result.append((query, parameters) if postgresql else (query, parameters, False))
    return result
