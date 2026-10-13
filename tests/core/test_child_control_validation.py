"""Cancellation validates reservations even before any action receipt exists."""

from __future__ import annotations

import json
import os
from dataclasses import replace

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def _plan(name):
    return compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "child-controls",
                "name": name,
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )


@pytest.fixture(params=["sqlite", "postgresql"])
def reserved_tree(tmp_path, request):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and datasource is None:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    plan = _plan("root")
    parent = services.invocations.create(
        engine="adk",
        session_id=None,
        user_id="correlation",
        owner_principal="owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    parent._execution_lease = services.execution_ownership.acquire(parent, seconds=60)
    identity = EffectIdentity(
        parent.invocation_id,
        parent.workflow_fingerprint,
        "/do/0/child",
        (("/do", 1),),
        "run:workflow",
    )
    child = services.children.reserve(identity, parent, _plan("child"), {"original": True})
    binding = services.children.get(identity)
    try:
        yield services, parent, child, identity, binding
    finally:
        services.invocations.update(parent, status="cancelled")
        services.execution_ownership.release(parent._execution_lease)
        services.close()


def test_control_validation_needs_no_catalog_or_effect_index(reserved_tree, monkeypatch):
    services, parent, child, identity, binding = reserved_tree

    def forbidden(*args, **kwargs):
        raise AssertionError("control must not resolve a catalog or invoke an action")

    monkeypatch.setattr(services.workflow_catalog, "resolve", forbidden)
    assert services.effect_journal.get(identity) is None
    assert services.child_waits.registration(identity) is None
    services.invocations.update(parent, status="cancelled")
    verified = services.children.verify_control(binding, parent)
    assert verified.invocation_id == child.invocation_id and verified.status == "running"
    assert services.model.calls == []


@pytest.mark.parametrize(
    "damage",
    [
        "missing-binding",
        "changed-binding",
        "missing-input",
        "changed-input",
        "budget-digest",
        "budget-count",
        "budget-limit",
        "owner",
        "user",
        "engine",
        "parent",
        "task-reference",
        "version",
        "name",
        "session",
        "forged-parent",
    ],
)
def test_control_rejects_changed_evidence_without_mutating_child(reserved_tree, damage):
    services, parent, child, _, binding = reserved_tree
    connection = services.children.connection
    if damage == "missing-binding":
        connection.execute(
            "DELETE FROM child_workflow_bindings WHERE binding_key=?", (binding.key,)
        )
    elif damage == "changed-binding":
        connection.execute(
            "UPDATE child_workflow_bindings SET depth=2 WHERE binding_key=?", (binding.key,)
        )
    elif damage == "missing-input":
        connection.execute(
            "DELETE FROM invocation_inputs WHERE invocation_id=?", (child.invocation_id,)
        )
    elif damage == "changed-input":
        connection.execute(
            "UPDATE invocation_inputs SET payload='null' WHERE invocation_id=?",
            (child.invocation_id,),
        )
    elif damage == "budget-digest":
        connection.execute(
            "UPDATE child_workflow_budgets SET policy_digest='missing' WHERE root_id=?",
            (parent.invocation_id,),
        )
    elif damage == "budget-count":
        connection.execute(
            "UPDATE child_workflow_budgets SET used=0 WHERE root_id=?", (parent.invocation_id,)
        )
    elif damage == "budget-limit":
        connection.execute(
            "UPDATE child_workflow_budgets SET max_children=1 WHERE root_id=?",
            (parent.invocation_id,),
        )
    elif damage == "forged-parent":
        parent.owner_principal = "forged"
    else:
        field, value = {
            "owner": ("owner_principal", "forged"),
            "user": ("user_id", "forged"),
            "engine": ("engine", "langgraph"),
            "parent": ("parent_invocation_id", child.invocation_id),
            "task-reference": ("parent_task_reference", "/do/9/forged"),
            "version": ("workflow_version", "2.0.0"),
            "name": ("workflow_name", "different"),
            "session": ("session_id", parent.session_id),
        }[damage]
        payload = json.loads(child._persisted_payload)
        payload[field] = value
        connection.execute(
            "UPDATE invocations SET payload=? WHERE invocation_id=?",
            (json.dumps(payload), child.invocation_id),
        )
    connection.commit()
    before = connection.execute(
        "SELECT payload,status FROM invocations WHERE invocation_id=?", (child.invocation_id,)
    ).fetchone()
    try:
        with pytest.raises(WorkflowExecutionError):
            services.children.verify_control(binding, parent)
        assert (
            connection.execute(
                "SELECT payload,status FROM invocations WHERE invocation_id=?",
                (child.invocation_id,),
            ).fetchone()
            == before
        )
        assert services.model.calls == []
    finally:
        if damage == "forged-parent":
            parent.owner_principal = "owner"


def test_control_proves_nested_ancestry_and_rejects_changed_depth(reserved_tree):
    services, root, child, _, _ = reserved_tree
    child._execution_lease = services.execution_ownership.acquire(child, seconds=60)
    identity = EffectIdentity(
        child.invocation_id,
        child.workflow_fingerprint,
        "/do/0/child",
        (("/do", 1),),
        "run:workflow",
    )
    try:
        grandchild = services.children.reserve(identity, child, _plan("grandchild"), {})
        binding = services.children.get(identity)
        assert (
            services.children.verify_control(binding, child).invocation_id
            == grandchild.invocation_id
        )
        forged = replace(binding, depth=1)
        connection = services.children.connection
        values = (
            forged.key,
            forged.root_id,
            forged.parent_id,
            forged.child_id,
            forged.depth,
            forged.request_digest,
            forged.workflow_source_json,
            forged.parent_task_reference,
        )
        connection.execute(
            "UPDATE child_workflow_bindings SET depth=?,digest=? WHERE binding_key=?",
            (forged.depth, services.children._digest(values), forged.key),
        )
        connection.commit()
        with pytest.raises(WorkflowExecutionError, match="ancestry"):
            services.children.verify_control(forged, child)
    finally:
        services.execution_ownership.release(child._execution_lease)
