"""Catalog results must agree with independently retained common memory evidence."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from open_workflow_agent.config import NativeMemoryBindingConfig
from open_workflow_agent.effects import EffectIdentity, EffectReceipt, effect_request_digest
from open_workflow_agent.memory_capacity import MemoryNamespaceBounds
from open_workflow_agent.memory_mutations import MemoryMutation
from open_workflow_agent.memory_reads import MemoryReadBinding, MemoryReadObservations
from open_workflow_agent.memory_search import MemorySearchQuery
from open_workflow_agent.memory_semantic import MemorySemanticIndex
from open_workflow_agent.native_memory_receipts import verify_catalog_receipt
from test_memory_writes import NativeEvidence


@pytest.fixture
def proof(services):
    handle = services.invocations.create(
        engine="test",
        session_id=None,
        user_id=None,
        owner_principal="alice",
        workflow_name="memory",
        workflow_version="1.0.0",
        workflow_fingerprint="definition",
    )
    handle._execution_lease = services.execution_ownership.acquire(handle, seconds=300)
    binding = NativeMemoryBindingConfig(id="notes", collection="notes")
    native = MemoryReadBinding("notes", "1.0.0", "sqlite")
    evidence = NativeEvidence(native)
    services.memory_writes.register(evidence.backend())
    namespace = "owa/principal-memory/v1:" + hashlib.sha256(b"alice").hexdigest()
    yield services, handle, binding, native, evidence, namespace
    services.execution_ownership.release(handle._execution_lease)


def parent_for(proof, operation, version="1.0.0"):
    handle = proof[1]
    return EffectIdentity(
        handle.invocation_id,
        handle.workflow_fingerprint,
        "/do/0/memory",
        (("/do/0/memory", 1),),
        f"call:memory.{operation}:{version}@notes",
    )


def receipt_for(parent, arguments, output):
    return EffectReceipt(
        effect_request_digest(parent.operation.removeprefix("call:"), arguments),
        "catalog-owner",
        status="completed",
        output=output,
    )


def verify(proof, parent, receipt, *, binding=None, semantic=None):
    services, _, installed, *_ = proof
    verify_catalog_receipt(
        services.effect_journal,
        services.invocations,
        services.memory_writes,
        [binding or installed],
        parent,
        receipt,
        semantic,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["put", "delete", "sweep"])
async def test_mutation_requires_its_exact_completed_leaf_and_catalog_input(proof, kind):
    services, handle, binding, native, evidence, namespace = proof
    parent = parent_for(proof, kind)
    arguments = (
        {"key": "record", "value": {"version": 1}}
        if kind == "put"
        else ({"key": "record"} if kind == "delete" else {})
    )
    mutation = (
        MemoryMutation.put(parent, native, namespace, binding.collection, "record", {"version": 1})
        if kind == "put"
        else MemoryMutation(parent, native, namespace, binding.collection, "record", "delete", None)
        if kind == "delete"
        else MemoryMutation(
            parent,
            native,
            namespace,
            binding.collection,
            "expired",
            "sweep",
            None,
            namespace_limits=MemoryNamespaceBounds("notes", "1.0.0"),
        )
    )
    receipt = receipt_for(parent, arguments, {"ok": True})
    with pytest.raises(ValueError, match="one retained mutation"):
        verify(proof, parent, receipt)
    await services.memory_writes.mutate(handle, mutation)
    verify(proof, parent, receipt)
    assert evidence.calls == [mutation.identity.key]
    for damaged in (
        replace(receipt, output={"ok": False}),
        replace(receipt, request_digest=effect_request_digest("different", arguments)),
    ):
        with pytest.raises(ValueError):
            verify(proof, parent, damaged)
    for change in ({"version": "1.0.1"}, {"collection": "other"}, {"max_record_bytes": 10}):
        with pytest.raises(ValueError, match="retained mutation proof"):
            verify(proof, parent, receipt, binding=binding.model_copy(update=change))
    with pytest.raises(ValueError, match="retained mutation proof"):
        verify(
            proof,
            parent,
            receipt,
            semantic={"notes": MemorySemanticIndex(artifact_sha256="a" * 64)},
        )
    services.effect_journal.connection.execute(
        "DELETE FROM workflow_effects WHERE identity=?", (mutation.identity.key,)
    )
    services.effect_journal.connection.commit()
    with pytest.raises(ValueError, match="retained mutation proof"):
        verify(proof, parent, receipt)
    assert evidence.calls == [mutation.identity.key]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["get", "search", "semantic"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_read_result_and_accepted_default_arguments_are_bound_to_observation(
    proof, kind, explicit
):
    services, handle, binding, native, _, namespace = proof
    semantic = MemorySemanticIndex(artifact_sha256="a" * 64) if kind == "semantic" else None
    query = (
        None
        if kind == "get"
        else MemorySearchQuery.create(
            {},
            binding.max_search_results,
            text="coffee" if semantic else None,
            index=semantic,
        )
    )
    parent = parent_for(
        proof, "get" if kind == "get" else "search", "1.1.0" if semantic else "1.0.0"
    )
    value = (
        {"color": "blue"}
        if query is None
        else {"items": [{"key": "record", "value": {"color": "blue"}}]}
    )
    arguments = (
        {"key": "record"}
        if query is None
        else ({"filter": {}, "limit": query.limit} if explicit else {})
    )
    if semantic:
        arguments["query"] = "coffee"
    receipt = receipt_for(parent, arguments, {"value": value} if query is None else value)
    profiles = {"notes": semantic} if semantic else None
    with pytest.raises(ValueError, match="retained observation"):
        verify(proof, parent, receipt, semantic=profiles)
    calls = []

    async def fetch():
        calls.append(True)
        return value

    await MemoryReadObservations(services.effect_journal, services.invocations).read(
        handle,
        parent,
        binding=native,
        namespace=namespace,
        collection=binding.collection,
        key="record" if query is None else query.key,
        max_record_bytes=binding.max_record_bytes,
        query=query,
        fetch=fetch,
    )
    verify(proof, parent, receipt, semantic=profiles)
    for damaged in (
        replace(receipt, output={"value": "forged"}),
        replace(receipt, request_digest=effect_request_digest("different", arguments)),
    ):
        with pytest.raises(ValueError):
            verify(proof, parent, damaged, semantic=profiles)
    for change in ({"version": "1.0.1"}, {"collection": "other"}, {"max_record_bytes": 10}):
        with pytest.raises(ValueError):
            verify(
                proof, parent, receipt, binding=binding.model_copy(update=change), semantic=profiles
            )
    if query is not None:
        with pytest.raises(ValueError, match="installed binding"):
            verify(
                proof,
                parent,
                receipt,
                binding=binding.model_copy(update={"max_search_results": 1}),
                semantic=profiles,
            )
    if semantic:
        with pytest.raises(ValueError, match="installed model/index"):
            verify(proof, parent, receipt)
    connection = services.effect_journal.connection
    row = connection.execute(
        "SELECT identity FROM memory_read_observations WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()
    connection.execute("DELETE FROM workflow_effects WHERE identity=?", (row[0],))
    connection.commit()
    with pytest.raises(ValueError, match="receipt is unavailable"):
        verify(proof, parent, receipt, semantic=profiles)
    assert calls == [True]


def test_noncompleted_uninstalled_or_unrecognized_calls_are_not_memory_proofs(proof):
    parent = parent_for(proof, "get")
    completed = receipt_for(parent, {"key": "record"}, {"value": None})
    verify(proof, parent, EffectReceipt(completed.request_digest, "owner"))
    verify(proof, replace(parent, operation="call:memory.get:1.0.0@other"), completed)
    verify(proof, parent_for(proof, "get", "1.1.0"), completed)
    with pytest.raises(ValueError, match="invocation is unavailable"):
        verify(proof, replace(parent, invocation_id="missing"), completed)
    with pytest.raises(ValueError, match="invocation is unavailable"):
        verify(proof, replace(parent, workflow_fingerprint="different"), completed)
