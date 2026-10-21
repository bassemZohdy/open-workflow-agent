"""Real common child identities consume atomic, persistent root-tree quotas."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import WorkflowExecutionError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def _plan(name: str):
    return compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "child-budgets",
                "name": name,
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )


@pytest.fixture(params=["sqlite", "postgresql"])
def tree(tmp_path, request) -> Iterator[tuple[RuntimeServices, Any, Any]]:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and datasource is None:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource},
            "child_workflows": {"max_depth": 2, "max_children": 3},
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    plan = _plan("root")
    root = services.invocations.create(
        engine="adk",
        session_id=None,
        user_id="correlation",
        owner_principal="principal",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    root._execution_lease = services.execution_ownership.acquire(root, seconds=30)
    try:
        yield services, root, _plan("child")
    finally:
        services.execution_ownership.release(root._execution_lease)
        services.close()


def _identity(parent, index=1):
    return EffectIdentity(
        parent.invocation_id,
        parent.workflow_fingerprint,
        "/do/0/child",
        (("/do", index),),
        "run:workflow",
    )


def test_child_reservation_is_immutable_and_consumes_quota_once(tree):
    services, parent, plan = tree
    identity = _identity(parent)
    first = services.children.reserve(identity, parent, plan, {"n": 3})
    repeated = services.children.reserve(identity, parent, plan, {"n": 3})
    assert repeated.invocation_id == first.invocation_id
    assert first.session_id != parent.session_id
    assert first.owner_principal == parent.owner_principal
    assert first.user_id == parent.user_id
    assert services.invocations.read_input(first) == {"n": 3}
    binding = services.children.get(identity)
    assert binding.root_id == parent.invocation_id and binding.depth == 1
    assert (
        services.children.connection.execute(
            "SELECT used FROM child_workflow_budgets WHERE root_id = ?", (parent.invocation_id,)
        ).fetchone()[0]
        == 1
    )
    with pytest.raises(WorkflowExecutionError, match="request changed"):
        services.children.reserve(identity, parent, plan, {"n": 2})
    assert services.model.calls == []


def test_depth_is_checked_before_creating_an_excess_child(tree):
    services, root, plan = tree
    child = services.children.reserve(_identity(root), root, plan, {})
    child._execution_lease = services.execution_ownership.acquire(child, seconds=30)
    grandchild = services.children.reserve(_identity(child), child, plan, {})
    grandchild._execution_lease = services.execution_ownership.acquire(grandchild, seconds=30)
    try:
        assert services.children.get(_identity(child)).depth == 2
        with pytest.raises(WorkflowExecutionError, match="budget exhausted"):
            services.children.reserve(_identity(grandchild), grandchild, plan, {})
        assert services.children.get(_identity(grandchild)) is None
        assert (
            services.children.connection.execute(
                "SELECT used FROM child_workflow_budgets WHERE root_id = ?", (root.invocation_id,)
            ).fetchone()[0]
            == 2
        )
    finally:
        services.execution_ownership.release(grandchild._execution_lease)
        services.execution_ownership.release(child._execution_lease)


def test_sibling_branches_share_the_total_child_budget(tree):
    services, root, plan = tree
    children = [
        services.children.reserve(_identity(root, index), root, plan, {}) for index in (1, 2)
    ]
    for child in children:
        child._execution_lease = services.execution_ownership.acquire(child, seconds=30)
    try:
        services.children.reserve(_identity(children[0]), children[0], plan, {})
        with pytest.raises(WorkflowExecutionError, match="budget exhausted"):
            services.children.reserve(_identity(children[1]), children[1], plan, {})
        assert services.children.get(_identity(children[1])) is None
        assert (
            services.children.connection.execute(
                "SELECT used FROM child_workflow_budgets WHERE root_id = ?", (root.invocation_id,)
            ).fetchone()[0]
            == 3
        )
    finally:
        for child in children:
            services.execution_ownership.release(child._execution_lease)


def test_child_identity_input_and_quota_roll_back_together(tree, monkeypatch):
    services, parent, plan = tree
    create = services.invocations.create_in_transaction
    created = []

    def fail_after_insert(*args, **kwargs):
        created.append(create(*args, **kwargs).invocation_id)
        raise WorkflowExecutionError("injected child reservation failure")

    monkeypatch.setattr(services.invocations, "create_in_transaction", fail_after_insert)
    with pytest.raises(WorkflowExecutionError, match="injected"):
        services.children.reserve(_identity(parent), parent, plan, {})
    assert created and services.invocations.get(created[0]) is None
    assert services.children.get(_identity(parent)) is None
    assert (
        services.children.connection.execute(
            "SELECT used FROM child_workflow_budgets WHERE root_id = ?", (parent.invocation_id,)
        ).fetchone()
        is None
    )


def test_reopen_and_policy_changes_do_not_reset_saved_root_quotas(tree):
    services, parent, plan = tree
    for index in (1, 2, 3):
        services.children.reserve(_identity(parent, index), parent, plan, {})
    config = services.config.model_copy(
        update={
            "child_workflows": services.config.child_workflows.model_copy(
                update={"max_depth": 64, "max_children": 100_000}
            )
        }
    )
    reopened = RuntimeServices(config, model=FakeModel(), database_root=services.database_root)
    try:
        first = reopened.children.reserve(_identity(parent), parent, plan, {})
        assert first.invocation_id == services.children.get(_identity(parent)).child_id
        with pytest.raises(WorkflowExecutionError, match="budget exhausted"):
            reopened.children.reserve(_identity(parent, 4), parent, plan, {})
        assert reopened.children.connection.execute(
            "SELECT max_depth,max_children,used FROM child_workflow_budgets WHERE root_id=?",
            (parent.invocation_id,),
        ).fetchone() == (2, 3, 3)
        assert reopened.model.calls == []
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "damage",
    ["budget", "budget-depth", "budget-children", "budget-digest", "binding", "input", "owner"],
)
def test_corrupt_child_evidence_rejects_reuse_before_dispatch(tree, damage):
    services, parent, plan = tree
    identity = _identity(parent)
    child = services.children.reserve(identity, parent, plan, {})
    connection = services.children.connection
    if damage == "budget":
        connection.execute(
            "UPDATE child_workflow_budgets SET used=0 WHERE root_id=?", (parent.invocation_id,)
        )
    elif damage == "budget-depth":
        connection.execute(
            "UPDATE child_workflow_budgets SET max_depth=3 WHERE root_id=?",
            (parent.invocation_id,),
        )
    elif damage == "budget-children":
        connection.execute(
            "UPDATE child_workflow_budgets SET max_children=4 WHERE root_id=?",
            (parent.invocation_id,),
        )
    elif damage == "budget-digest":
        connection.execute(
            "UPDATE child_workflow_budgets SET policy_digest='missing' WHERE root_id=?",
            (parent.invocation_id,),
        )
    elif damage == "binding":
        connection.execute(
            "UPDATE child_workflow_bindings SET depth=2 WHERE binding_key=?", (identity.key,)
        )
    elif damage == "input":
        connection.execute(
            "DELETE FROM invocation_inputs WHERE invocation_id=?", (child.invocation_id,)
        )
    else:
        payload = json.loads(child._persisted_payload)
        payload["owner_principal"] = "forged"
        connection.execute(
            "UPDATE invocations SET payload=? WHERE invocation_id=?",
            (json.dumps(payload), child.invocation_id),
        )
    connection.commit()
    with pytest.raises(WorkflowExecutionError):
        services.children.reserve(identity, parent, plan, {})
    if damage.startswith("budget"):
        with pytest.raises(WorkflowExecutionError):
            services.children.reserve(_identity(parent, 2), parent, plan, {})
        assert services.children.get(_identity(parent, 2)) is None
    assert services.model.calls == []


def test_unpublished_budget_schema_is_preserved_without_trusting_unverified_limits(tmp_path):
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    plan = _plan("root")
    parent = services.invocations.create(
        engine="adk",
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    parent._execution_lease = services.execution_ownership.acquire(parent, seconds=30)
    try:
        child = services.children.reserve(_identity(parent), parent, plan, {})
        services.children.connection.execute(
            "ALTER TABLE child_workflow_budgets DROP COLUMN policy_digest"
        )
        services.children.connection.commit()
        reopened = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
        try:
            assert reopened.children.connection.execute(
                "SELECT policy_digest,used FROM child_workflow_budgets WHERE root_id=?",
                (parent.invocation_id,),
            ).fetchone() == (None, 1)
            assert reopened.invocations.get(child.invocation_id) is not None
            with pytest.raises(WorkflowExecutionError, match="budget"):
                reopened.children.reserve(_identity(parent), parent, plan, {})
            with pytest.raises(WorkflowExecutionError, match="budget"):
                reopened.children.reserve(_identity(parent, 2), parent, plan, {})
            assert reopened.children.get(_identity(parent, 2)) is None
        finally:
            reopened.close()
    finally:
        services.execution_ownership.release(parent._execution_lease)
        services.close()
