"""Public replay is opt-in, bounded, authenticated and invisible by default."""

import pytest
from open_workflow_agent.config import NativeStateReplayConfig, RuntimeConfig
from open_workflow_agent.replay_control_api import ReplayStateRequest, state_replay_capabilities
from pydantic import ValidationError


def test_default_serialization_preserves_existing_configuration_shape():
    config = RuntimeConfig()
    assert config.native_state_replay is None
    assert "native_state_replay" not in config.model_dump(mode="json")
    assert "native_state_replay" not in config.model_dump_json()


@pytest.mark.parametrize(
    "value",
    [{}, {"native_state": {}}, {"native_state": {}, "server": {"api_security_profile": "missing"}}],
)
def test_replay_requires_state_views_authenticated_ingress_and_authorization(value):
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate(value | {"native_state_replay": {}})


def test_replay_does_not_require_state_updates():
    value = {
        "native_state": {},
        "native_state_replay": {},
        "server": {"api_security_profile": "actor"},
        "security": {
            "profiles": {
                "actor": {
                    "type": "bearer",
                    "principal": "actor",
                    "token": {"from_env": "OWA_UNUSED_REPLAY_TOKEN"},
                }
            }
        },
        "execution_authorization": {"enabled": True},
    }
    config = RuntimeConfig.model_validate(value)
    assert config.native_state_updates is None
    assert config.native_state_replay == NativeStateReplayConfig()


@pytest.mark.parametrize(
    "value",
    [
        {"max_active_replays": 0},
        {"max_active_replays": 17},
        {"max_active_replays": True},
        {"max_replays_per_source": 0},
        {"max_replays_per_source": 65},
        {"max_replays_per_source": True},
        {"bounds": {"max_response_bytes": 256}},
        {"bounds": {"max_native_bytes": 67_108_864, "max_concurrency": 2}},
        {"bounds": {"timeout_seconds": float("nan")}},
    ],
)
def test_limits_are_finite_strict_and_account_for_staged_packets(value):
    with pytest.raises(ValidationError):
        NativeStateReplayConfig.model_validate(value)


def test_read_and_replay_budgets_share_the_configured_ceiling():
    value = {
        "native_state": {"max_native_bytes": 67_108_864, "max_concurrency": 4},
        "native_state_replay": {},
        "server": {"api_security_profile": "actor"},
        "security": {
            "profiles": {
                "actor": {
                    "type": "bearer",
                    "principal": "actor",
                    "token": {"from_env": "OWA_UNUSED_REPLAY_TOKEN"},
                }
            }
        },
        "execution_authorization": {"enabled": True},
    }
    with pytest.raises(ValidationError, match="combined native state"):
        RuntimeConfig.model_validate(value)


def test_client_cannot_supply_native_identifiers_or_malformed_references():
    assert ReplayStateRequest(reference="1" * 64).acknowledge_reexecution is False
    for bad in (
        {"reference": "short"},
        {"reference": "1" * 64, "checkpoint_id": "private"},
        {"reference": "1" * 64, "acknowledge_reexecution": "yes"},
    ):
        with pytest.raises(ValidationError):
            ReplayStateRequest(**bad)


def test_capability_advertises_only_what_is_enforced():
    advertised = state_replay_capabilities()
    assert advertised["branch"] is False and advertised["timeTravel"] is False
    assert advertised["scope"] == "root" and advertised["checkpoints"] == "completed_task_prefix"
    assert advertised["actions"] == ["state.replay"]
