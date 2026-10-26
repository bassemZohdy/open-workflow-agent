from __future__ import annotations

import json

import pytest
from open_workflow_agent.config import NativeStateConfig, RuntimeConfig
from open_workflow_agent.native_state import CAPABILITY, NativeStateQuery, NativeStateResult


@pytest.mark.parametrize(
    "values",
    [
        {"kind": "debug"},
        {"limit": True},
        {"limit": 101},
        {"limit": 0},
        {"reference": "native-checkpoint-id"},
        {"before": "f" * 64},
        {"limit": 2},
        {"kind": "history", "reference": "f" * 64},
        {"reference": "A" * 64},
    ],
)
def test_invalid_or_ambiguous_state_queries_are_rejected(values):
    with pytest.raises(ValueError):
        NativeStateQuery(**values)


@pytest.mark.parametrize(
    "bounds",
    [
        {"max_history": 0},
        {"max_page_size": True},
        {"max_history": 1},
        {"max_native_bytes": 1023},
        {"max_concurrency": 33},
        {"max_concurrency": 32, "max_native_bytes": 67_108_864},
        {"max_response_bytes": 255},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
    ],
)
def test_state_capacity_and_deadline_configuration_is_bounded(bounds):
    with pytest.raises(ValueError):
        NativeStateConfig(**bounds)


def test_default_configuration_serializes_without_native_state_policy():
    config = RuntimeConfig()
    assert config.native_state is None
    assert "native_state" not in config.model_dump(mode="json")
    with pytest.raises(ValueError, match="authenticated"):
        RuntimeConfig.model_validate({"native_state": {}})


def test_state_results_return_detached_json_without_native_objects():
    result = NativeStateResult(json.dumps({"profile": CAPABILITY, "items": [{"data": []}]}))
    first = result.as_dict()
    first["items"][0]["data"].append("changed")
    assert result.as_dict()["items"][0]["data"] == []
    for value in ("[]", "{}", '{"profile":"unsupported"}'):
        with pytest.raises(ValueError):
            NativeStateResult(value)
