"""Read-only retention and early admission for the finite protected profile."""

from __future__ import annotations

import asyncio
import copy
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity, effect_request_digest
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import OwaError
from open_workflow_agent.recovery_definitions import RecoveryDefinitionArchive
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def runtime(path, monkeypatch, *, protected=True):
    monkeypatch.setenv("OWA_RETENTION_ACTOR", "controlled-retention-token")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "security": {
                "profiles": {
                    "actor": {
                        "type": "bearer",
                        "principal": "reviewer",
                        "token": {"from_env": "OWA_RETENTION_ACTOR"},
                    }
                }
            },
            "approvals": {
                "enabled": True,
                "protected": protected,
                "actor_security_profiles": ["actor"] if protected else [],
            },
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=path)
    assert services.event_store.connection.kind == "sqlite"
    # This fixture proves the common proof/retention seam. Independent actual
    # SDK and PostgreSQL qualification lives in the shared native fixtures.
    services.recovery_definitions = RecoveryDefinitionArchive(
        services.effect_journal,
        services.invocations,
        profile_provider=lambda engine: {
            "profile": "test-only/common-approval-retention/v1",
            "engine": engine,
            "backend": "sqlite",
            "continuation_supported": True,
        },
    )
    return services


def event(subject):
    return {
        "id": subject,
        "source": "urn:fixture:approval-retention",
        "subject": subject,
        "type": "io.openworkflow.agent.approval.requested",
        "approvalexpiresat": datetime.fromtimestamp(time.time() + 300, UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "data": {
            "approvalPolicy": {
                "profile": "owa/protected-approval/v1",
                "assignee": "reviewer",
                "reassignmentTargets": [],
                "escalationTargets": [],
            }
        },
    }


def workflow(properties):
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "approval-recovery",
            "name": "original",
            "version": "1.0.0",
        },
        "do": [{"request": {"emit": {"event": {"with": properties}}}}],
    }


def handle(services, plan):
    return services.invocations.create(
        engine="portable",
        session_id=None,
        user_id="requester",
        owner_principal="requester",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [False, True], ids=["valid", "orphan-hint"])
async def test_prune_retains_unconsumed_terminal_control_without_listener_or_outbox_pin(
    tmp_path, monkeypatch, damage
):
    services = runtime(tmp_path, monkeypatch)
    subject = str(uuid4())
    properties = event(subject)
    plan = compile_workflow(workflow(properties))
    invocation = handle(services, plan)
    invocation._execution_lease = services.execution_ownership.acquire(invocation, seconds=300)
    services.invocations.update(invocation, status="running")
    services.recovery_definitions.admit(
        plan, invocation, {"original": None}, execution_lease=invocation._execution_lease
    )
    reference = "/do/0/request"
    identity = EffectIdentity(
        invocation.invocation_id,
        plan.fingerprint,
        reference,
        ((reference, 1), (reference + "/@attempt", 1)),
        "emit",
    )
    try:
        await services.event_bus.publish_workflow_event(
            identity,
            effect_request_digest(
                "emit",
                {
                    "properties": properties,
                    "default_source": "urn:fixture:approval-retention",
                    "scope": "requester",
                },
            ),
            properties,
            default_source="urn:fixture:approval-retention",
            scope="requester",
            execution_lease=invocation._execution_lease,
        )
        assert services.event_store.deliver_outbox(limit=8) == 1
        principal = services.approvals.authorize_actor("Bearer controlled-retention-token")
        decision = await services.approvals.decide(
            subject,
            decision="approved",
            operator_id=principal.identity,
            actor=principal,
            value=None,
            revision=1,
            operation_key=str(uuid4()),
        )
        connection = services.event_store.connection
        terminal_id = decision.decision_operation_key
        assert connection.execute("SELECT 1 FROM event_selections LIMIT 1").fetchone() is None
        assert (
            connection.execute("SELECT 1 FROM event_consumer_selections LIMIT 1").fetchone() is None
        )
        assert connection.execute(
            "SELECT envelope FROM event_outbox WHERE scope=?", ("requester",)
        ).fetchall() == [
            (
                services.event_store.connection.execute(
                    "SELECT envelope FROM event_inbox WHERE event_id=? AND scope=?",
                    (subject, "requester"),
                ).fetchone()[0],
            )
        ]
        ordinary = services.event_store.ingest(
            {"id": str(uuid4()), "type": "fixture.ordinary", "data": None},
            default_source="urn:fixture:ordinary",
            scope="requester",
        )
        if damage:
            connection.execute(
                "DELETE FROM "
                + services.approvals.protected_store.requests
                + " WHERE approval_id=?",
                (subject,),
            )
            connection.commit()
        assert (
            services.event_store.prune(
                now=time.time() + services.event_store.limits.retention_seconds + 1
            )
            == 1
        )
        assert (
            connection.execute(
                "SELECT 1 FROM event_inbox WHERE event_id=?", (ordinary.id,)
            ).fetchone()
            is None
        )
        terminal = connection.execute(
            "SELECT envelope FROM event_inbox WHERE event_id=? AND scope=?",
            (terminal_id, "requester"),
        ).fetchone()
        assert terminal is not None
        if damage:
            with pytest.raises(OwaError):
                services.verify_protected_approvals(invocation)
        else:
            services.verify_protected_approvals(invocation)
            assert services.approvals.store.get(subject).decision_event == decision.decision_event
        assert services.model.calls == []
    finally:
        services.execution_ownership.release(invocation._execution_lease)
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("unsupported", ["disabled", "undeclared-actor", "relative-expiry"])
async def test_literal_protected_policy_rejects_before_earlier_action_or_owner(
    tmp_path, monkeypatch, unsupported
):
    services = runtime(tmp_path, monkeypatch, protected=unsupported != "disabled")
    properties = event(str(uuid4()))
    if unsupported == "undeclared-actor":
        properties["data"]["approvalPolicy"]["assignee"] = "outsider"
    elif unsupported == "relative-expiry":
        properties["approvalexpiresat"] = "tomorrow"
    source = workflow(properties)
    source["do"].insert(0, {"before": {"set": {"wouldAdvance": True}}})
    plan = compile_workflow(source)
    invocation = handle(services, plan)
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    advances = []
    original = engine.executor._run_task_item

    async def observe(*args, **kwargs):
        advances.append(args)
        return await original(*args, **kwargs)

    engine.executor._run_task_item = observe
    try:
        with pytest.raises(OwaError):
            await asyncio.wait_for(engine.invoke(plan, invocation, copy.deepcopy({"raw": None})), 5)
        assert not advances and services.model.calls == []
        assert (
            services.execution_ownership.connection.execute(
                "SELECT 1 FROM invocation_ownership WHERE invocation_id=?",
                (invocation.invocation_id,),
            ).fetchone()
            is None
        )
        assert (
            services.effect_journal.connection.execute(
                "SELECT 1 FROM workflow_effect_invocations WHERE invocation_id=?",
                (invocation.invocation_id,),
            ).fetchone()
            is None
        )
    finally:
        await engine.shutdown()
        services.close()
