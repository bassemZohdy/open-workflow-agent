"""Record expiry policy is typed, versioned, immutable and part of write identity."""

from __future__ import annotations

from dataclasses import replace

import pytest
from open_workflow_agent.effects import EffectIdentity, effect_request_digest
from open_workflow_agent.memory_mutations import PROFILE, MemoryMutation
from open_workflow_agent.memory_reads import MemoryReadBinding
from open_workflow_agent.memory_retention import PROFILE as RETENTION_PROFILE
from open_workflow_agent.memory_retention import MemoryRecordRetention


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        0,
        -1,
        31_536_001,
        1.5,
        float("nan"),
        float("inf"),
        "60",
        None,
        pytest.param(10**2000, id="huge"),
    ],
)
def test_invalid_ttl_rejects_without_a_provider(value):
    with pytest.raises(ValueError):
        MemoryRecordRetention("records", "1", value)


@pytest.mark.parametrize(
    "field,value", [("id", ""), ("id", "../x"), ("version", "x" * 129), ("version", True)]
)
def test_policy_identity_is_bounded(field, value):
    with pytest.raises(ValueError):
        MemoryRecordRetention(
            **{**{"id": "records", "version": "1", "ttl_seconds": 60}, field: value}
        )


@pytest.mark.parametrize("changed", ["profile", "extra", "missing", "null"])
def test_unknown_or_incomplete_retention_metadata_rejects(changed):
    raw = MemoryRecordRetention("records", "1", 60).descriptor
    if changed == "profile":
        raw["profile"] = "owa/memory-record-retention/v2"
    elif changed == "extra":
        raw["clock"] = "caller"
    elif changed == "missing":
        del raw["ttl_seconds"]
    else:
        raw = None
    with pytest.raises(ValueError):
        MemoryRecordRetention.from_descriptor(raw)


def mutation():
    return MemoryMutation.put(
        EffectIdentity("invocation", "definition", "/do/write", (("/do/write", 1),), "call:agent"),
        MemoryReadBinding("test", "1", "sqlite"),
        "owa/principal-memory/v1:" + "0" * 64,
        "preferences",
        "record",
        {"x": True},
    )


def test_absent_policy_preserves_legacy_descriptor_and_request_digest():
    request = mutation()
    legacy = {
        "profile": PROFILE,
        "parent": {
            "invocation_id": "invocation",
            "workflow_fingerprint": "definition",
            "task_reference": "/do/write",
            "occurrence": [["/do/write", 1]],
            "operation": "call:agent",
        },
        "binding": {"id": "test", "version": "1", "backend": "sqlite"},
        "namespace": "owa/principal-memory/v1:" + "0" * 64,
        "collection": "preferences",
        "key": "record",
        "kind": "put",
        "value": {"x": True},
        "max_record_bytes": 65_536,
    }
    assert request.descriptor == legacy
    assert request.request_digest == effect_request_digest(PROFILE + ":request", legacy)
    assert MemoryMutation.from_descriptor(legacy) == request


def test_versioned_policy_roundtrip_and_changes_keep_slot_but_change_digest():
    request = mutation()
    retained = replace(request, retention=MemoryRecordRetention("records", "1", 60))
    assert retained.descriptor["retention"]["profile"] == RETENTION_PROFILE
    assert MemoryMutation.from_descriptor(retained.descriptor) == retained
    assert request.identity == retained.identity
    assert request.request_digest != retained.request_digest
    for policy in [
        MemoryRecordRetention("records", "1", 61),
        MemoryRecordRetention("records", "2", 60),
    ]:
        assert replace(retained, retention=policy).request_digest != retained.request_digest
    raw = retained.descriptor
    raw["retention"]["ttl_seconds"] = 10
    assert retained.retention.ttl_seconds == 60
