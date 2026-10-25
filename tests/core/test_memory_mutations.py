"""Private mutation requests remain framework-neutral and finite."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.memory_mutations import MemoryMutation
from open_workflow_agent.memory_reads import MemoryReadBinding
from open_workflow_agent.ownership import ExecutionOwnershipStore
from open_workflow_agent.persistence import InvocationStore


@pytest.mark.parametrize("value", [[], {"x": float("nan")}, {"x": 2**53}, {"x": "雪" * 40}])
def test_invalid_value_has_no_mutation_request(value):
    parent = EffectIdentity(
        "invocation", "definition", "/do/write", (("/do/write", 1),), "call:agent"
    )
    with pytest.raises(InvocationStateError):
        MemoryMutation.put(
            parent,
            MemoryReadBinding("test", "1", "sqlite"),
            "owa/principal-memory/v1:" + "0" * 64,
            "preferences",
            "record",
            value,
            max_record_bytes=64,
        )


def test_mutation_request_keeps_a_copy_of_caller_data_and_descriptor():
    parent = EffectIdentity(
        "invocation", "definition", "/do/write", (("/do/write", 1),), "call:agent"
    )
    value = {"nested": ["original"]}
    mutation = MemoryMutation.put(
        parent,
        MemoryReadBinding("test", "1", "sqlite"),
        "owa/principal-memory/v1:" + "0" * 64,
        "preferences",
        "record",
        value,
    )
    digest = mutation.request_digest
    value["nested"].append("caller mutation")
    descriptor = mutation.descriptor
    descriptor["value"]["nested"].append("descriptor mutation")
    assert mutation.value_json == '{"nested":["original"]}'
    assert mutation.request_digest == digest


@pytest.mark.parametrize("encoded", ['{"x":1,"x":2}', '{"x":NaN}', "[]", "null"])
def test_raw_noncanonical_or_nonobject_value_rejects_before_storage(encoded):
    parent = EffectIdentity(
        "invocation", "definition", "/do/write", (("/do/write", 1),), "call:agent"
    )
    with pytest.raises(InvocationStateError):
        MemoryMutation(
            parent,
            MemoryReadBinding("test", "1", "sqlite"),
            "owa/principal-memory/v1:" + "0" * 64,
            "preferences",
            "record",
            "put",
            encoded,
        )


@pytest.mark.parametrize("changed", [None, "principal", "namespace", "lease", "status", "identity"])
def test_original_running_owned_invocation_is_required_without_an_engine(tmp_path, changed):
    path = str(tmp_path / "common.sqlite3")
    invocations = InvocationStore(path)
    ownership = ExecutionOwnershipStore(path)
    try:
        handle = invocations.create(
            engine="test",
            session_id=None,
            user_id=None,
            owner_principal="alice",
            workflow_name="memory",
            workflow_version="1.0.0",
            workflow_fingerprint="definition",
        )
        handle._execution_lease = ownership.acquire(handle)
        namespace = "owa/principal-memory/v1:" + hashlib.sha256(b"alice").hexdigest()
        parent = EffectIdentity(
            handle.invocation_id,
            handle.workflow_fingerprint,
            "/do/write",
            (("/do/write", 1),),
            "call:agent",
        )
        mutation = MemoryMutation.put(
            parent,
            MemoryReadBinding("test", "1", "sqlite"),
            namespace,
            "preferences",
            "record",
            {"x": True},
        )
        mutation.verify_invocation(invocations, handle)
        if changed is None:
            return
        if changed in {"principal", "namespace"}:
            mutation = replace(
                mutation, namespace="owa/principal-memory/v1:" + hashlib.sha256(b"bob").hexdigest()
            )
            if changed == "principal":
                handle.owner_principal = "bob"
        elif changed == "lease":
            handle._execution_lease = None
        elif changed == "status":
            handle.status = "completed"
        else:
            handle.workflow_version = "2.0.0"
        with pytest.raises(InvocationStateError):
            mutation.verify_invocation(invocations, handle)
    finally:
        ownership.close()
        invocations.close()
