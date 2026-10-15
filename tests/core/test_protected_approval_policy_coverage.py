"""Finite protected-approval policy edges with no runtime or network dependencies."""

from __future__ import annotations

import math

import pytest
from open_workflow_agent.errors import ApprovalValidationError
from open_workflow_agent.protected_approval_policy import (
    PROTECTED_APPROVAL_PROFILE,
    ProtectedApprovalPolicy,
    actor_identity,
    bounded_json,
    original_expiry,
)


def _policy(**overrides):
    return {
        "profile": PROTECTED_APPROVAL_PROFILE,
        "assignee": "operator-a",
        "reassignmentTargets": ["operator-b"],
        "escalationTargets": ["supervisor"],
        **overrides,
    }


def test_bounded_values_and_actor_ids_reject_non_finite_or_internal_inputs() -> None:
    assert bounded_json({"approved": True}) == '{"approved": true}'
    with pytest.raises(ApprovalValidationError, match="finite JSON profile"):
        bounded_json({"number": math.nan})
    with pytest.raises(ApprovalValidationError, match="actor identity"):
        actor_identity("system:expiry-worker")
    assert actor_identity("system:expiry-worker", internal=True) == "system:expiry-worker"


@pytest.mark.parametrize(
    "value",
    ["2026-01-01", "2026-02-30T10:00:00Z", "0000-01-01T00:00:00Z", 123],
)
def test_original_expiry_rejects_relative_or_impossible_timestamps(value) -> None:
    with pytest.raises(ApprovalValidationError, match="absolute RFC 3339 UTC"):
        original_expiry(value)


def test_original_expiry_accepts_only_valid_absolute_utc_values() -> None:
    assert original_expiry("2026-10-14T12:30:00Z") > 0


@pytest.mark.parametrize("value", [None, [], {"profile": "other"}])
def test_declared_actor_scan_rejects_invalid_policy_envelopes(value) -> None:
    with pytest.raises(ApprovalValidationError, match="policy is invalid"):
        ProtectedApprovalPolicy.declared_actors(value)


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("prefix ${ .actor } suffix", "assignment targets are invalid"),
        ("${", "policy expression is invalid"),
        ("${ }", "policy expression is invalid"),
    ],
)
def test_declared_actor_scan_rejects_malformed_dynamic_targets(expression, message) -> None:
    selected = _policy(
        reassignmentTargets=expression if expression.startswith("prefix") else [expression],
        escalationTargets=[],
    )
    with pytest.raises(ApprovalValidationError, match=message):
        ProtectedApprovalPolicy.declared_actors(selected)


def test_declared_actor_scan_omits_dynamic_assignment_and_bounds_static_lists() -> None:
    actors = ProtectedApprovalPolicy.declared_actors(
        _policy(assignee="${ .actor }", reassignmentTargets=["operator-b"], escalationTargets=[])
    )
    assert actors == frozenset({"operator-b"})
    with pytest.raises(ApprovalValidationError, match="assignment targets are invalid"):
        ProtectedApprovalPolicy.declared_actors(
            _policy(reassignmentTargets=[f"operator-{index}" for index in range(33)])
        )


def test_response_schema_is_validated_at_admission_and_against_each_response() -> None:
    selected = ProtectedApprovalPolicy.from_value(
        _policy(
            responseSchema={
                "format": "json",
                "document": {"type": "object", "required": ["decision"]},
            }
        )
    )
    selected.validate_response({"decision": "yes"})
    with pytest.raises(ApprovalValidationError, match="does not satisfy"):
        selected.validate_response({"other": "value"})
    with pytest.raises(ApprovalValidationError, match="schema is invalid or unsupported"):
        ProtectedApprovalPolicy.from_value(
            _policy(responseSchema={"format": "yaml", "document": {"type": "string"}})
        )
