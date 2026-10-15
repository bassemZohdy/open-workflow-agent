"""Compiled dynamic approval data stays subject to atomic actor admission."""

from __future__ import annotations

import copy
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import OwaError
from open_workflow_agent.protected_approval_policy import PROTECTED_APPROVAL_PROFILE
from open_workflow_agent.recovery_definitions import PROFILE
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


@pytest.fixture(params=["sqlite", "postgresql"])
async def dynamic_approval(tmp_path, monkeypatch, request):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and not datasource:
        pytest.skip("actual PostgreSQL is required for atomic approval admission")
    reviewer = "reviewer-" + uuid4().hex
    variable = "OWA_DYNAMIC_ACTOR_" + uuid4().hex.upper()
    monkeypatch.setenv(variable, "controlled-dynamic-actor-token")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "security": {
                "profiles": {
                    "actor": {
                        "type": "bearer",
                        "principal": reviewer,
                        "token": {"from_env": variable},
                    }
                }
            },
            "approvals": {"enabled": True, "protected": True, "actor_security_profiles": ["actor"]},
        }
    )
    services = RuntimeServices(
        config,
        model=FakeModel({"unreachable": True}),
        database_root=None if datasource else tmp_path,
    )
    assert services.effect_journal.connection.kind == request.param
    # This is a common-store fixture, not a physical SDK/deployment oracle.
    services.recovery_definitions.profile_provider = lambda engine: {
        "profile": PROFILE,
        "engine": engine,
        "backend": request.param,
        "continuation_supported": True,
        "qualification": "test-only/dynamic-approval",
    }
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    try:
        yield services, engine, reviewer
    finally:
        await engine.shutdown()
        services.close()


def source(subject, *, policy=None, earlier_business=False, later_business=False):
    event = {
        "id": subject,
        "subject": subject,
        "source": "urn:contract:dynamic-approval",
        "type": "io.openworkflow.agent.approval.requested",
        "approvalexpiresat": (datetime.now(UTC) + timedelta(seconds=300))
        .isoformat()
        .replace("+00:00", "Z"),
        "data": {
            "approvalPolicy": policy
            or {
                "profile": PROTECTED_APPROVAL_PROFILE,
                "assignee": "${ $workflow.input.assignee }",
                "reassignmentTargets": "${ $workflow.input.reassignmentTargets // [] }",
                "escalationTargets": "${ $workflow.input.escalationTargets // [] }",
            }
        },
    }
    business = {"probe": {"call": "llm:1.0.0@default", "with": {"input": "must not run"}}}
    tasks = ([business] if earlier_business else []) + [
        {"request": {"emit": {"event": {"with": event}}}}
    ]
    if later_business:
        tasks.append(business)
    return {
        "document": {"dsl": "1.0.3", "namespace": "tests", "name": subject, "version": "1.0.0"},
        "input": {"schema": {"format": "json", "document": {"type": "object"}}},
        "do": tasks,
    }


def handle(services, plan):
    return services.invocations.create(
        engine="reference",
        session_id=None,
        user_id="correlation-only",
        owner_principal="requester-" + uuid4().hex,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


def request_rows(services, target, subject):
    protected = services.approvals.protected_store
    assert protected is not None
    connection = services.effect_journal.connection
    return (
        connection.execute(
            f"SELECT * FROM {services.approvals.store._table} WHERE approval_id=?", (subject,)
        ).fetchall(),
        connection.execute(
            f"SELECT * FROM {protected.requests} WHERE approval_id=?", (subject,)
        ).fetchall(),
        connection.execute(
            f"SELECT * FROM {protected.audit} WHERE approval_id=?", (subject,)
        ).fetchall(),
        connection.execute("SELECT * FROM event_inbox WHERE event_id=?", (subject,)).fetchall(),
        connection.execute(
            "SELECT * FROM event_outbox WHERE invocation_id=?", (target.invocation_id,)
        ).fetchall(),
    )


async def test_valid_dynamic_actor_data_is_retained_as_literal_owned_policy(dynamic_approval):
    services, engine, reviewer = dynamic_approval
    subject = uuid4().hex
    original = {"assignee": reviewer, "reassignmentTargets": [reviewer], "escalationTargets": []}
    plan = compile_workflow(source(subject))
    services.verify_protected_approval_plan(plan)
    target = handle(services, plan)
    result = await engine.invoke(plan, target, original)
    assert result.status == "completed", result.error
    captured = services.approvals.protected_store.inspect(subject)[0]
    assert captured["policy"] == {"profile": PROTECTED_APPROVAL_PROFILE, **original}
    assert captured["invocation_binding"]["owner_principal"] == target.owner_principal
    assert services.invocations.read_input(target) == original
    assert services.approvals.store.get(subject).status == "pending"
    assert services.model.calls == []


@pytest.mark.parametrize(
    "invalid",
    [
        "assignee-unbound",
        "assignee-nonstring",
        "targets-not-list",
        "targets-unbound",
        "targets-duplicate",
        "targets-overbound",
        "escalation-unbound",
    ],
)
async def test_evaluated_policy_rejects_atomically_before_protected_rows_or_later_business(
    dynamic_approval, invalid
):
    services, engine, reviewer = dynamic_approval
    subject = uuid4().hex
    original = {"assignee": reviewer, "reassignmentTargets": [], "escalationTargets": []}
    if invalid == "assignee-unbound":
        original["assignee"] = "unbound-actor"
    elif invalid == "assignee-nonstring":
        original["assignee"] = 7
    elif invalid == "targets-not-list":
        original["reassignmentTargets"] = reviewer
    elif invalid == "targets-unbound":
        original["reassignmentTargets"] = ["unbound-actor"]
    elif invalid == "targets-duplicate":
        original["reassignmentTargets"] = [reviewer, reviewer]
    elif invalid == "targets-overbound":
        original["reassignmentTargets"] = [str(index) for index in range(33)]
    else:
        original["escalationTargets"] = ["unbound-actor"]
    plan = compile_workflow(source(subject, later_business=True))
    target = handle(services, plan)
    before = request_rows(services, target, subject)
    result = await engine.invoke(plan, target, copy.deepcopy(original))
    assert result.status == "faulted" and result.error["code"] == "approval_validation_error"
    assert request_rows(services, target, subject) == before
    assert services.approvals.store.get(subject) is None
    assert services.model.calls == [] and services.invocations.read_input(target) == original


@pytest.mark.parametrize(
    "invalid", ["profile", "extra", "unbound-literal", "invalid-literal", "target-interpolation"]
)
async def test_dynamic_siblings_do_not_bypass_static_policy_rejection_before_ownership(
    dynamic_approval, invalid
):
    services, engine, reviewer = dynamic_approval
    subject = uuid4().hex
    policy = {
        "profile": PROTECTED_APPROVAL_PROFILE,
        "assignee": "${ $workflow.input.assignee }",
        "reassignmentTargets": [reviewer],
        "escalationTargets": [],
    }
    if invalid == "profile":
        policy["profile"] = "${ $workflow.input.profile }"
    elif invalid == "extra":
        policy["authority"] = "untrusted"
    elif invalid == "unbound-literal":
        policy["reassignmentTargets"] = ["unbound-actor"]
    elif invalid == "invalid-literal":
        policy["reassignmentTargets"] = None
    else:
        policy["reassignmentTargets"] = "prefix-${ $workflow.input.reassignmentTargets }"
    plan = compile_workflow(source(subject, policy=policy, earlier_business=True))
    target = handle(services, plan)
    before = request_rows(services, target, subject)
    with pytest.raises(OwaError):
        await engine.invoke(plan, target, {"assignee": reviewer})
    assert request_rows(services, target, subject) == before
    assert services.model.calls == []
    assert (
        services.execution_ownership.connection.execute(
            "SELECT 1 FROM invocation_ownership WHERE invocation_id=?", (target.invocation_id,)
        ).fetchone()
        is None
    )
    assert not services.recovery_definitions.has_binding(target)


async def test_dynamic_policy_still_requires_enabled_protected_service(dynamic_approval):
    services, engine, reviewer = dynamic_approval
    services.config.approvals.protected = False
    plan = compile_workflow(source(uuid4().hex, earlier_business=True))
    target = handle(services, plan)
    with pytest.raises(OwaError):
        await engine.invoke(plan, target, {"assignee": reviewer})
    assert services.model.calls == []
    assert (
        services.execution_ownership.connection.execute(
            "SELECT 1 FROM invocation_ownership WHERE invocation_id=?", (target.invocation_id,)
        ).fetchone()
        is None
    )
