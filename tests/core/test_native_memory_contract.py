"""Native memory adds explicit bounded config without changing default profiles."""

from __future__ import annotations

import pytest
from open_workflow_agent.config import MemoryConfig, NativeMemoryBindingConfig
from open_workflow_agent.memory_search import MemorySearchQuery


def test_default_serialization_omits_native_bindings():
    assert "native" not in MemoryConfig().model_dump(mode="json")


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "../x"},
        {"collection": "X"},
        {"version": "2.0.0"},
        {"max_records": True},
        {"max_records": 0},
        {"max_value_bytes": 67_108_865},
        {"cleanup_limit": 65},
        {"max_search_results": False},
        {"max_record_bytes": 0},
        {"retention_seconds": 0},
        {"retention_seconds": 1.5},
        {"read_timeout_seconds": True},
        {"provider": "untrusted"},
    ],
)
def test_binding_rejects_unbounded_or_external_selectors(changes):
    with pytest.raises(ValueError):
        NativeMemoryBindingConfig.model_validate({"id": "notes", "collection": "notes", **changes})


@pytest.mark.parametrize("case", ["disabled", "id", "collection", "count"])
def test_native_bindings_are_explicit_finite_and_unique(case):
    a = {"id": "a", "collection": "a"}
    b = {"id": "b", "collection": "b"}
    value = {"native": [a, b]}
    if case == "disabled":
        value["enabled"] = False
    elif case == "count":
        value["native"] = [{"id": "n" + str(i), "collection": "n" + str(i)} for i in range(17)]
    else:
        b[case] = a[case]
    with pytest.raises(ValueError):
        MemoryConfig.model_validate(value)


@pytest.mark.parametrize(
    "filter,limit",
    [
        ([], 2),
        ({"x": {}}, 2),
        ({"x.y": "x"}, 2),
        ({"x": float("nan")}, 2),
        ({"x": 2**53}, 2),
        ({"x": "x" * 5000}, 2),
        ({str(i): i for i in range(17)}, 2),
        ({}, True),
        ({}, 0),
        ({}, 65),
    ],
)
def test_search_request_rejects_unsafe_or_unbounded_inputs(filter, limit):
    with pytest.raises(ValueError):
        MemorySearchQuery.create(filter, limit)


def test_search_descriptor_is_canonical_copied_and_exact():
    a = MemorySearchQuery.create({"b": None, "a": True}, 2)
    b = MemorySearchQuery.create({"a": True, "b": None}, 2)
    assert a == b and a.key == b.key
    assert MemorySearchQuery.from_descriptor(a.descriptor) == a
    value = a.filter
    value["a"] = False
    assert a.filter["a"] is True
    for descriptor in (a.descriptor | {"profile": "v2"}, a.descriptor | {"query": "semantic"}):
        with pytest.raises(ValueError):
            MemorySearchQuery.from_descriptor(descriptor)
