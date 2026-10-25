"""Namespace admission requires finite typed policy and a distinct cleanup slot."""

from __future__ import annotations

from dataclasses import replace

import pytest
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.memory_capacity import MemoryNamespaceBounds
from open_workflow_agent.memory_mutations import MemoryMutation
from open_workflow_agent.memory_reads import MemoryReadBinding


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", ""),
        ("id", "../x"),
        ("version", True),
        ("version", "x" * 129),
        ("max_records", True),
        ("max_records", 0),
        ("max_records", 10_001),
        ("max_value_bytes", 0),
        ("max_value_bytes", 67_108_865),
        ("max_value_bytes", float("inf")),
        ("cleanup_limit", False),
        ("cleanup_limit", 0),
        ("cleanup_limit", 65),
        ("cleanup_limit", 1.5),
    ],
)
def test_invalid_namespace_bounds_reject_without_storage(field, value):
    with pytest.raises(ValueError):
        MemoryNamespaceBounds(**{**{"id": "records", "version": "1"}, field: value})


@pytest.mark.parametrize("changed", ["profile", "extra", "missing", "null"])
def test_unknown_or_incomplete_profile_rejects(changed):
    raw = MemoryNamespaceBounds("records", "1").descriptor
    if changed == "profile":
        raw["profile"] = "owa/memory-namespace-bounds/v2"
    elif changed == "extra":
        raw["prefix"] = "caller"
    elif changed == "missing":
        del raw["cleanup_limit"]
    else:
        raw = None
    with pytest.raises(ValueError):
        MemoryNamespaceBounds.from_descriptor(raw)


def request():
    return MemoryMutation.put(
        EffectIdentity("invocation", "definition", "/do/write", (("/do/write", 1),), "call:agent"),
        MemoryReadBinding("test", "1", "sqlite"),
        "owa/principal-memory/v1:" + "0" * 64,
        "preferences",
        "record",
        {"x": True},
    )


def test_policy_roundtrip_copy_and_digest_bind_every_limit():
    policy = MemoryNamespaceBounds("records", "1", 3, 1000, 2)
    assert MemoryNamespaceBounds.from_descriptor(policy.descriptor) == policy
    original = request()
    bounded = replace(original, namespace_limits=policy)
    assert original.identity == bounded.identity
    assert original.request_digest != bounded.request_digest
    assert MemoryMutation.from_descriptor(bounded.descriptor) == bounded
    for changed in (
        replace(policy, version="2"),
        replace(policy, max_records=4),
        replace(policy, max_value_bytes=1001),
        replace(policy, cleanup_limit=3),
    ):
        assert changed.digest != policy.digest
        assert replace(bounded, namespace_limits=changed).request_digest != bounded.request_digest
    raw = bounded.descriptor
    raw["namespace_limits"]["max_records"] = 100
    assert bounded.namespace_limits.max_records == 3
    assert "namespace_limits" not in original.descriptor


def test_cleanup_has_a_distinct_slot_and_requires_its_fixed_bounded_scope():
    original = request()
    with pytest.raises(InvocationStateError):
        replace(original, kind="sweep", key="expired", value_json=None)
    bounded = replace(original, namespace_limits=MemoryNamespaceBounds("records", "1"))
    sweep = replace(bounded, kind="sweep", key="expired", value_json=None)
    assert (
        sweep.identity != replace(bounded, kind="delete", key="expired", value_json=None).identity
    )
    assert MemoryMutation.from_descriptor(sweep.descriptor) == sweep
    with pytest.raises(InvocationStateError):
        replace(sweep, key="another")
    with pytest.raises(InvocationStateError):
        replace(bounded, namespace_limits={"cleanup_limit": 100})
