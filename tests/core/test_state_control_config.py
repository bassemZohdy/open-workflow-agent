"""Public controls require explicit authenticated policy and bounded resources."""

import pytest
from open_workflow_agent.config import NativeStateUpdateConfig, RuntimeConfig
from open_workflow_agent.state_control_api import ResolveStateRequest, UpdateStateRequest
from pydantic import ValidationError


def test_default_serialization_preserves_existing_configuration_shape():
    config = RuntimeConfig()
    assert "native_state_updates" not in config.model_dump(mode="json")
    assert "native_state_updates" not in config.model_dump_json()


@pytest.mark.parametrize(
    "value",
    [{}, {"native_state": {}}, {"native_state": {}, "server": {"api_security_profile": "missing"}}],
)
def test_updates_require_state_views_authenticated_ingress_and_authorization(value):
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate(value | {"native_state_updates": {}})


@pytest.mark.parametrize(
    "value",
    [
        {"max_workflow_bindings": 0},
        {"max_workflow_bindings": True},
        {"bounds": {"max_response_bytes": 256}},
        {"bounds": {"max_native_bytes": 67_108_864, "max_concurrency": 2}},
        {"bounds": {"timeout_seconds": float("nan")}},
    ],
)
def test_limits_are_finite_strict_and_account_for_staged_packets(value):
    with pytest.raises(ValidationError):
        NativeStateUpdateConfig.model_validate(value)


@pytest.mark.parametrize(
    "value", [{"value": float("nan")}, {"value": b"bytes"}, {"value": "x" * 65536}]
)
def test_untrusted_values_are_finite_bounded_json(value):
    with pytest.raises(ValidationError):
        UpdateStateRequest(base_reference="1" * 64, task_reference="/do/0/first", values=value)


def test_client_cannot_supply_native_identifiers_or_internal_proof():
    with pytest.raises(ValidationError):
        UpdateStateRequest(
            base_reference="1" * 64,
            task_reference="/do/0/first",
            values={},
            checkpoint_id="private",
        )
    with pytest.raises(ValidationError):
        ResolveStateRequest(request_digest="1" * 64, decision="complete", native_proof={})


def test_read_and_update_budgets_share_the_configured_ceiling():
    value = {
        "native_state": {"max_native_bytes": 67_108_864, "max_concurrency": 4},
        "native_state_updates": {},
        "server": {"api_security_profile": "actor"},
        "security": {
            "profiles": {
                "actor": {
                    "type": "bearer",
                    "principal": "actor",
                    "token": {"from_env": "OWA_UNUSED_STATE_TOKEN"},
                }
            }
        },
        "execution_authorization": {"enabled": True},
    }
    with pytest.raises(ValidationError, match="combined native state"):
        RuntimeConfig.model_validate(value)
