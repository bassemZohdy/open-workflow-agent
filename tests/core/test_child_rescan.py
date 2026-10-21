"""Fork rescan authority comes from real immutable common child decisions."""

from __future__ import annotations

import copy
import os
from dataclasses import replace

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.child_rescan import child_rescan_wake, verify_child_rescan_wake
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, ReceiptDependency
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

_FORK = "/do/0/race"


def _plan(name):
    return compile_workflow(
        {
            "document": {"dsl": "1.0.3", "namespace": "rescan", "name": name, "version": "1.0.0"},
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )


@pytest.fixture(params=["sqlite", "postgresql"])
def fork_tree(tmp_path, request):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and datasource is None:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    plan = _plan("parent")
    parent = services.invocations.create(
        engine="adk",
        session_id=None,
        user_id="correlation",
        owner_principal="owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    services.invocations.bind_input(parent, {"original": True})
    parent._execution_lease = services.execution_ownership.acquire(parent, seconds=300)
    dependencies = {}

    def new_wait(branch, *, fork=_FORK, visit=1, nested=False, leaf="start"):
        base = fork + f"/fork/branches/{branch}"
        occurrence = [(fork, visit), (fork + "/@attempt", 1)]
        if nested:
            inner = base + "/inner"
            occurrence += [(inner, 1), (inner + "/@attempt", 1)]
            reference = inner + f"/fork/branches/0/{leaf}"
        else:
            reference = base + f"/{leaf}"
        occurrence += [(reference, 1), (reference + "/@attempt", 1)]
        identity = EffectIdentity(
            parent.invocation_id,
            parent.workflow_fingerprint,
            reference,
            tuple(occurrence),
            "run:workflow",
        )
        child = services.children.reserve(identity, parent, _plan("child"), {"branch": branch})
        services.invocations.update(child, status="waiting")
        registration = services.child_waits.arm(
            identity, parent, child, workflow_source=plan.source, deadline_at=None
        )
        admission = replace(identity, operation="policy:child-await:v1")
        dependencies[admission.key] = ReceiptDependency.capture(admission, registration.receipt)
        return registration

    def completed_wake(registration):
        child = services.invocations.get(registration.suspension.child_id)
        services.invocations.update(child, status="running")
        services.invocations.update(
            child, status="completed", output={"ready": child.invocation_id}
        )
        return services.child_waits.wake(registration, parent, now=500.0)

    common = {
        "child_waits": services.child_waits,
        "journal": services.effect_journal,
        "dependencies": dependencies,
        "invocation_id": parent.invocation_id,
        "workflow_fingerprint": parent.workflow_fingerprint,
    }
    try:
        yield services, parent, new_wait, completed_wake, common
    finally:
        services.invocations.update(parent, status="cancelled")
        services.execution_ownership.release(parent._execution_lease)
        services.close()


def _ready_pair(tree):
    _, _, new_wait, complete, _ = tree
    pending, sibling = new_wait(0), new_wait(1)
    return pending, sibling, complete(sibling)


def _transport(pending, sibling, wake):
    return {
        "profile": "owa/child-rescan/v1",
        "pending": pending.suspension.as_value(),
        "ready_wakes": {sibling.identity.key: wake},
    }


def test_verified_sibling_rescan_does_not_wake_interrupted_child(fork_tree):
    services, parent, _, _, common = fork_tree
    pending, sibling, wake = _ready_pair(fork_tree)
    original = pending.suspension.as_value()
    value = child_rescan_wake(
        pending.suspension, ready_wakes={sibling.identity.key: wake}, **common
    )
    assert value == _transport(pending, sibling, wake)
    assert verify_child_rescan_wake(value, pending.suspension, **common) == value
    assert services.child_waits.known_wakes(parent.invocation_id, parent.workflow_fingerprint) == {
        sibling.identity.key: wake
    }
    assert (
        services.effect_journal.get(replace(pending.identity, operation="policy:child-wake:v1"))
        is None
    )
    assert services.invocations.get(pending.suspension.child_id).status == "waiting"
    value["pending"]["child_id"] = "forged"
    assert pending.suspension.as_value() == original
    assert services.model.calls == []


@pytest.mark.parametrize(
    "damage", ["profile", "extra", "pending", "output", "deadline-bool", "empty", "missing-key"]
)
def test_modified_rescan_transport_cannot_authorize_reentry(fork_tree, damage):
    _, _, _, _, common = fork_tree
    pending, sibling, wake = _ready_pair(fork_tree)
    value = copy.deepcopy(_transport(pending, sibling, wake))
    if damage == "profile":
        value["profile"] = "owa/child-rescan/v2"
    elif damage == "extra":
        value["allow"] = True
    elif damage == "pending":
        value["pending"] = sibling.suspension.as_value()
    elif damage == "output":
        value["ready_wakes"][sibling.identity.key]["request_digest"] = "0" * 64
    elif damage == "deadline-bool":
        value["ready_wakes"][sibling.identity.key]["deadline_at"] = False
    elif damage == "empty":
        value["ready_wakes"] = {}
    else:
        value["ready_wakes"] = {"0" * 64: wake}
    with pytest.raises(InvocationStateError):
        verify_child_rescan_wake(value, pending.suspension, **common)


@pytest.mark.parametrize(
    "damage",
    [
        "pending-native-admission",
        "sibling-native-admission",
        "forged-dependency",
        "common-admission",
        "wake",
        "registration",
    ],
)
def test_missing_or_changed_common_and_native_provenance_fails_closed(fork_tree, damage):
    services, _, _, _, common = fork_tree
    pending, sibling, wake = _ready_pair(fork_tree)
    pending_admission = replace(pending.identity, operation="policy:child-await:v1")
    sibling_admission = replace(sibling.identity, operation="policy:child-await:v1")
    connection = services.effect_journal.connection
    if damage == "pending-native-admission":
        del common["dependencies"][pending_admission.key]
    elif damage == "sibling-native-admission":
        del common["dependencies"][sibling_admission.key]
    elif damage == "forged-dependency":
        common["dependencies"][sibling_admission.key] = replace(
            common["dependencies"][sibling_admission.key], receipt_digest="0" * 64
        )
    elif damage == "common-admission":
        connection.execute(
            "DELETE FROM workflow_effects WHERE identity=?", (sibling_admission.key,)
        )
    elif damage == "wake":
        connection.execute(
            "DELETE FROM workflow_effects WHERE identity=?",
            (replace(sibling.identity, operation="policy:child-wake:v1").key,),
        )
    else:
        connection.execute(
            "DELETE FROM child_workflow_waits WHERE run_key=?", (sibling.identity.key,)
        )
    connection.commit()
    with pytest.raises(InvocationStateError):
        verify_child_rescan_wake(_transport(pending, sibling, wake), pending.suspension, **common)


@pytest.mark.parametrize("relation", ["other-fork", "other-visit", "same-branch"])
def test_recorded_wake_from_unrelated_execution_cannot_rescan_pending_branch(fork_tree, relation):
    _, _, new_wait, complete, common = fork_tree
    pending = new_wait(0)
    sibling = (
        new_wait(1, fork="/do/1/other")
        if relation == "other-fork"
        else (new_wait(1, visit=2) if relation == "other-visit" else new_wait(0, leaf="later"))
    )
    wake = complete(sibling)
    assert (
        child_rescan_wake(pending.suspension, ready_wakes={sibling.identity.key: wake}, **common)
        is None
    )
    with pytest.raises(InvocationStateError):
        verify_child_rescan_wake(_transport(pending, sibling, wake), pending.suspension, **common)


def test_consumed_sibling_wake_verifies_history_but_cannot_trigger_another_rescan(fork_tree):
    services, parent, _, _, common = fork_tree
    pending, sibling, wake = _ready_pair(fork_tree)
    value = child_rescan_wake(
        pending.suspension, ready_wakes={sibling.identity.key: wake}, **common
    )
    services.child_waits.consume(sibling, parent)
    assert verify_child_rescan_wake(value, pending.suspension, **common) == value
    assert (
        child_rescan_wake(pending.suspension, ready_wakes={sibling.identity.key: wake}, **common)
        is None
    )


def test_nested_pending_child_uses_common_outer_fork_sibling_authority(fork_tree):
    services, parent, new_wait, complete, common = fork_tree
    pending, sibling = new_wait(0, nested=True), new_wait(1)
    wake = complete(sibling)
    value = child_rescan_wake(
        pending.suspension, ready_wakes={sibling.identity.key: wake}, **common
    )
    assert value == _transport(pending, sibling, wake)
    assert (
        verify_child_rescan_wake(value, pending.suspension, fork_reference=_FORK, **common) == value
    )
    assert services.child_waits.known_wakes(parent.invocation_id, parent.workflow_fingerprint) == {
        sibling.identity.key: wake
    }
    inner_fork = _FORK + "/fork/branches/0/inner"
    assert (
        child_rescan_wake(
            pending.suspension,
            ready_wakes={sibling.identity.key: wake},
            fork_reference=inner_fork,
            **common,
        )
        is None
    )
    with pytest.raises(InvocationStateError):
        verify_child_rescan_wake(value, pending.suspension, fork_reference=inner_fork, **common)


def test_current_native_owner_and_saved_workflow_provenance_must_match(fork_tree):
    _, _, _, _, common = fork_tree
    pending, sibling, wake = _ready_pair(fork_tree)
    value = _transport(pending, sibling, wake)
    for replacement in (
        {"invocation_id": sibling.suspension.child_id},
        {"workflow_fingerprint": "0" * 64},
    ):
        with pytest.raises(InvocationStateError):
            verify_child_rescan_wake(value, pending.suspension, **{**common, **replacement})
    with pytest.raises(InvocationStateError):
        verify_child_rescan_wake(
            value, pending.suspension, fork_reference="/do/99/unrelated", **common
        )


def test_sibling_admission_without_any_actual_wake_cannot_authorize_reentry(fork_tree):
    services, parent, new_wait, _, common = fork_tree
    pending, sibling = new_wait(0), new_wait(1)
    assert child_rescan_wake(pending.suspension, ready_wakes=None, **common) is None
    forged = {
        "child_key": sibling.identity.key,
        "child_id": sibling.suspension.child_id,
        "request_digest": sibling.suspension.request_digest,
        "deadline_at": None,
        "reason": "child_terminal",
    }
    with pytest.raises(InvocationStateError):
        verify_child_rescan_wake(_transport(pending, sibling, forged), pending.suspension, **common)
    assert services.child_waits.known_wakes(parent.invocation_id, parent.workflow_fingerprint) == {}
