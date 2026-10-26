"""Verify public catalog results against their durable common memory evidence."""

from __future__ import annotations

import json
from collections.abc import Sequence

from .config import NativeMemoryBindingConfig
from .effects import EffectIdentity, EffectJournal, EffectReceipt, effect_request_digest
from .memory_mutations import MemoryMutation
from .memory_reads import (
    MAX_READS,
    MAX_REQUEST_BYTES,
    MemoryReadBinding,
    _bounded_column,
    _load,
    _request,
    _table_exists,
)
from .memory_search import MemorySearchQuery
from .memory_writes import MemoryWrites
from .persistence import InvocationStore


def verify_mutation_parent(request: MemoryMutation, receipt: EffectReceipt) -> None:
    reference = f"memory.{request.kind}:1.0.0@{request.binding.id}"
    payload = (
        {"key": request.key, "value": json.loads(request.value_json)}
        if request.kind == "put" and request.value_json is not None
        else {"key": request.key}
        if request.kind == "delete"
        else {}
    )
    if (
        request.parent.operation != "call:" + reference
        or receipt.request_digest != effect_request_digest(reference, payload)
    ):
        raise ValueError("memory intent differs from its enclosing catalog request")


def verify_catalog_receipt(
    journal: EffectJournal,
    invocations: InvocationStore,
    writes: MemoryWrites,
    bindings: Sequence[NativeMemoryBindingConfig],
    parent: EffectIdentity,
    receipt: EffectReceipt,
) -> None:
    # Pending calls re-enter only the attested handler and its own coordinator.
    # Validation failures can legitimately occur before any leaf was admitted.
    if receipt.status != "completed":
        return
    binding = next(
        (item for item in bindings if parent.operation.endswith(":1.0.0@" + item.id)), None
    )
    if binding is None:
        return
    operation = parent.operation.removeprefix("call:memory.").split(":", 1)[0]
    if parent.operation != f"call:memory.{operation}:1.0.0@{binding.id}":
        return
    handle = invocations.get(parent.invocation_id)
    if handle is None or handle.workflow_fingerprint != parent.workflow_fingerprint:
        raise ValueError("memory invocation is unavailable")
    if operation in {"put", "delete", "sweep"}:
        matches = [request for request, _ in writes.requests(handle) if request.parent == parent]
        if len(matches) != 1:
            raise ValueError("completed memory call requires one retained mutation")
        request = matches[0]
        verify_mutation_parent(request, receipt)
        child = journal.get(request.identity)
        if (
            request.binding.id != binding.id
            or request.binding.version != binding.version
            or request.collection != binding.collection
            or request.max_record_bytes != binding.max_record_bytes
            or child is None
            or child.status != "completed"
            or receipt.output != {"ok": True}
        ):
            raise ValueError("completed memory result differs from retained mutation proof")
    elif operation in {"get", "search"}:
        connection = journal.connection
        if not _table_exists(connection):
            raise ValueError("completed memory call requires its retained observation")
        rows = connection.execute(
            "SELECT "
            + _bounded_column(connection, "request_json", MAX_REQUEST_BYTES)
            + " FROM memory_read_observations WHERE invocation_id=? LIMIT ?",
            (parent.invocation_id, MAX_READS + 1),
        ).fetchall()
        if len(rows) > MAX_READS:
            raise ValueError("memory observation limit exceeded")
        read_matches = []
        for (encoded,) in rows:
            if type(encoded) is not str:
                raise ValueError("memory observation request is invalid")
            descriptor = json.loads(encoded)
            if EffectIdentity.from_value(descriptor["parent"]) == parent:
                read_matches.append(descriptor)
        if len(read_matches) != 1:
            raise ValueError("completed memory call requires one retained observation")
        descriptor = read_matches[0]
        query = (
            MemorySearchQuery.from_descriptor(descriptor["query"])
            if "query" in descriptor
            else None
        )
        child_identity, _, _ = _request(
            parent,
            MemoryReadBinding(**descriptor["binding"]),
            descriptor["namespace"],
            descriptor["collection"],
            descriptor["key"],
            descriptor["max_record_bytes"],
            query,
        )
        child = journal.get(child_identity)
        if child is None:
            raise ValueError("memory observation receipt is unavailable")
        _, value_json = _load(connection, invocations, child_identity, child)
        value = json.loads(value_json)
        if operation == "get":
            inputs = [{"key": descriptor["key"]}]
            output = {"value": value}
        else:
            if query is None or query.limit > binding.max_search_results:
                raise ValueError("memory search differs from its installed binding")
            inputs = [{"filter": query.filter, "limit": query.limit}]
            if not query.filter:
                inputs.append({"limit": query.limit})
            if query.limit == binding.max_search_results:
                inputs.extend(
                    {k: v for k, v in item.items() if k != "limit"} for item in list(inputs)
                )
            output = value
        if (
            (query is not None) != (operation == "search")
            or descriptor["binding"]["id"] != binding.id
            or descriptor["binding"]["version"] != binding.version
            or descriptor["collection"] != binding.collection
            or descriptor["max_record_bytes"] != binding.max_record_bytes
            or receipt.output != output
            or receipt.request_digest
            not in {
                effect_request_digest(parent.operation.removeprefix("call:"), item)
                for item in inputs
            }
        ):
            raise ValueError("completed memory result differs from its retained observation")
