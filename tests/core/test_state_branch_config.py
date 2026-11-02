"""Native branch installation is opt-in and shares a bounded memory budget."""

import pytest
from open_workflow_agent.config import (
    NativeStateBranchConfig,
    NativeStateConfig,
    NativeStateReplayConfig,
    NativeStateUpdateConfig,
    RuntimeConfig,
)
from pydantic import ValidationError


def test_branch_configuration_is_opt_in_and_requires_state_controls():
    assert "native_state_branch" not in RuntimeConfig().model_dump()
    branch = NativeStateBranchConfig()
    with pytest.raises(ValidationError):
        RuntimeConfig(native_state_branch=branch)
    with pytest.raises(ValidationError):
        RuntimeConfig(native_state=NativeStateConfig(), native_state_branch=branch)


def test_combined_state_lineage_budget_cannot_exceed_capacity():
    # Security profiles are validated separately; the capacity failure is checked first.
    with pytest.raises(ValidationError, match="combined native state decoding and lineage budget"):
        RuntimeConfig(
            native_state=NativeStateConfig(),
            native_state_updates=NativeStateUpdateConfig(),
            native_state_replay=NativeStateReplayConfig(),
            native_state_branch=NativeStateBranchConfig(),
        )


@pytest.mark.parametrize("field", ["max_active_branches", "max_branches_per_source"])
def test_branch_limits_are_positive(field):
    with pytest.raises(ValidationError):
        NativeStateBranchConfig(**{field: 0})


def test_active_branches_cannot_exceed_native_decode_concurrency():
    with pytest.raises(ValidationError, match="active branches exceed"):
        NativeStateBranchConfig(max_active_branches=3)
