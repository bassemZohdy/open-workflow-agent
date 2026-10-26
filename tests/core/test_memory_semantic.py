"""Semantic model/query identities are bounded and preserve the v1 codec."""

from dataclasses import replace

import pytest
from open_workflow_agent.config import NativeMemoryBindingConfig, NativeMemorySemanticConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.memory_mutations import MemoryMutation
from open_workflow_agent.memory_reads import MemoryReadBinding
from open_workflow_agent.memory_search import MemorySearchQuery
from open_workflow_agent.memory_semantic import MemorySemanticIndex


def test_nonsemantic_binding_and_query_preserve_original_serialization():
    assert "semantic" not in NativeMemoryBindingConfig(id="notes", collection="notes").model_dump()
    assert MemorySearchQuery().descriptor == {
        "profile": "owa/memory-search/v1",
        "filter": {},
        "limit": 20,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"profile": "external"},
        {"field": "a.b"},
        {"max_text_bytes": True},
        {"max_text_bytes": 16385},
        {"timeout_seconds": float("inf")},
        {"provider": "remote"},
        {"artifact_sha256": "not-a-digest"},
    ],
)
def test_semantic_settings_reject_unbounded_or_undeclared_models(changes):
    with pytest.raises(ValueError):
        NativeMemorySemanticConfig.model_validate(changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"dimensions": 384.0},
        {"dimensions": 385},
        {"distance": "l2"},
        {"index": "approximate"},
        {"model": "remote"},
        {"artifact_sha256": ""},
    ],
)
def test_index_descriptor_requires_exact_model_profile(changes):
    policy = MemorySemanticIndex(artifact_sha256="a" * 64)
    with pytest.raises(ValueError):
        MemorySemanticIndex.from_descriptor(policy.descriptor | changes)


def test_semantic_query_roundtrip_copies_filter_and_binds_text_and_model():
    policy = MemorySemanticIndex(artifact_sha256="a" * 64)
    query = MemorySearchQuery.create({"kind": "note"}, 3, text="coffee", index=policy)
    assert MemorySearchQuery.from_descriptor(query.descriptor) == query
    assert query.key != replace(query, text="tea").key
    assert query.key != replace(query, index=replace(policy, artifact_sha256="b" * 64)).key
    assert query.descriptor["profile"] == "owa/memory-search/v2"
    changed = query.filter
    changed["kind"] = "other"
    assert query.filter == {"kind": "note"}


@pytest.mark.parametrize("text", [None, "", "  ", "雪" * 1366, True])
def test_semantic_query_requires_bounded_nonempty_text(text):
    with pytest.raises(ValueError):
        MemorySearchQuery.create(text=text, index=MemorySemanticIndex(artifact_sha256="a" * 64))


def test_semantic_mutation_binds_index_identity_without_changing_ordinary_requests():
    parent = EffectIdentity(
        "invocation", "definition", "/do/0", (("/do/0", 1),), "call:memory.put:1.0.0@notes"
    )
    args = (
        parent,
        MemoryReadBinding("notes", "1.0.0", "sqlite"),
        "owa/principal-memory/v1:" + "0" * 64,
        "notes",
        "record",
        {"text": "coffee"},
    )
    ordinary = MemoryMutation.put(*args)
    policy = MemorySemanticIndex(artifact_sha256="a" * 64)
    indexed = MemoryMutation.put(*args, semantic=policy)
    assert "semantic" not in ordinary.descriptor
    assert ordinary.identity == indexed.identity
    assert ordinary.request_digest != indexed.request_digest
    assert MemoryMutation.from_descriptor(indexed.descriptor) == indexed
    with pytest.raises(InvocationStateError):
        MemoryMutation.put(*args, semantic=MemorySemanticIndex())
    with pytest.raises(InvocationStateError):
        MemoryMutation.put(*args[:-1], {"text": ""}, semantic=policy)
