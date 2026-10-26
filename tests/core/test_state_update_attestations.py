"""Versioned control commands preserve old persisted meanings and exact digest pins."""

from __future__ import annotations

from dataclasses import replace

import pytest
from open_workflow_agent.errors import OwaError
from open_workflow_agent.state_updates import StateUpdateRequest


def request():
    return StateUpdateRequest.create(
        invocation_id="inv",
        workflow_fingerprint="workflow",
        request_id="1" * 64,
        base_reference="2" * 64,
        task_reference="/do/0/set",
        values={"value": 7},
        policy_digest="3" * 64,
        storage_identity="4" * 64,
    )


def test_legacy_command_descriptor_is_unchanged_and_new_attestation_is_versioned():
    old = request()
    assert old.profile == "owa/state-update/v1"
    assert set(old.descriptor) == {
        "profile",
        "invocation_id",
        "workflow_fingerprint",
        "request_id",
        "base_reference",
        "task_reference",
        "values_json",
        "policy_digest",
        "storage_identity",
    }
    assert StateUpdateRequest.from_descriptor(old.descriptor) == old
    new = replace(old, base_digest="5" * 64, projection_digest="6" * 64)
    assert new.identity == old.identity and new.request_digest != old.request_digest
    assert new.profile == "owa/state-update/v2"
    assert StateUpdateRequest.from_descriptor(new.descriptor) == new
    assert new.descriptor["previous_controls"] == []
    with pytest.raises(OwaError):
        StateUpdateRequest.from_descriptor(new.descriptor | {"profile": old.profile})
    with pytest.raises(OwaError):
        StateUpdateRequest.from_descriptor(old.descriptor | {"profile": new.profile})


@pytest.mark.parametrize(
    "changes",
    [
        {"base_digest": "5" * 64},
        {"projection_digest": "6" * 64},
        {"base_digest": "invalid", "projection_digest": "6" * 64},
        {"base_digest": "5" * 64, "projection_digest": "6" * 64, "previous_controls": ["7" * 64]},
        {"base_digest": "5" * 64, "projection_digest": "6" * 64, "previous_controls": ([],)},
        {
            "base_digest": "5" * 64,
            "projection_digest": "6" * 64,
            "previous_controls": ("7" * 64,) * 2,
        },
        {"base_digest": "5" * 64, "projection_digest": "6" * 64, "previous_controls": ("1" * 64,)},
    ],
)
def test_attestation_and_ordered_lineage_are_complete_finite_and_distinct(changes):
    with pytest.raises(OwaError):
        replace(request(), **changes)
