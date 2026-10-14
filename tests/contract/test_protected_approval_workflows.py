"""Actual native protected approval waiting/control/recovery on SQLite and PostgreSQL."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from uuid import uuid4

import pytest
from _protected_approval_fixtures import (
    ENGINES,
    actor,
    actual_native,
    approval_snapshot,
    configuration,
    definition,
    expiry,
    handle,
    identities,
    runtime,
    waiting,
)
from _version_recovery_worker import native_snapshot
from open_workflow_agent.errors import (
    ApprovalAuthorizationError,
    ApprovalConflict,
    ApprovalValidationError,
    OwaError,
)
from open_workflow_agent.protected_approval_store import MUTATION_POLICY, REQUEST_POLICY
from open_workflow_agent.workflow import compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")
RAW = {"original": None, "nested": [True, False]}


async def decide(
    services, subject, principal, status="approved", *, value=None, revision=1, key=None
):
    return await services.approvals.decide(
        subject,
        decision=status,
        operator_id=principal.identity,
        actor=principal,
        value=value,
        revision=revision,
        operation_key=key or str(uuid4()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "case",
    ["approved", "rejected", "expired", "reassign", "escalate", "schema", "null", "race", "reopen"],
)
async def test_original_protected_approval_controls_gate_one_native_business_action(
    tmp_path, engine_name, engine_type, backend, case
):
    business = []
    services = runtime(tmp_path, backend, business=business)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    engine._execution_ownership_seconds = 0.5 if case == "expired" else 30.0
    if case == "expired":
        await services.stop_protected_approvals()
    subject = str(uuid4())
    plan = compile_workflow(
        definition(subject, expiry(0.5 if case == "expired" else 120), schema=case == "schema")
    )
    invocation = handle(services, engine_name, plan)
    task = asyncio.create_task(engine.invoke(plan, invocation, copy.deepcopy(RAW)))
    duplicate = None
    expected, value, reviewer = "approved", {"ticket": "S305"}, actor(services, "reviewer-a")
    try:
        await waiting(services, invocation, task)
        request = services.approvals.store.get(subject)
        assert (
            request.status == "pending"
            and request.assignee == "reviewer-a"
            and request.assignment_revision == 1
        )
        assert business == [] and services.model.calls == []
        original_request, _ = services.approvals.protected_store.inspect(subject)
        assert original_request["invocation_binding"]["owner_principal"] == "requester"
        assert original_request["event_scope"] == "requester"
        assert services.invocations.read_input(invocation) == RAW
        original_expiry = request.expires_at
        if case == "expired":
            async with asyncio.timeout(5):
                while time.time() < original_expiry:
                    await asyncio.sleep(0.01)
            assert services.approvals.store.get(subject).status == "pending"
            await services.start_protected_approvals()
            expected, value = "expired", None
        elif case == "rejected":
            expected, value = "rejected", {"reason": "denied"}
            await decide(services, subject, reviewer, expected, value=value)
        elif case in {"reassign", "escalate"}:
            target = "reviewer-b" if case == "reassign" else "lead"
            changed = await services.approvals.mutate_protected(
                subject,
                action=case,
                actor=reviewer,
                revision=1,
                operation_key=str(uuid4()),
                assignee=target,
            )
            assert (
                changed.assignee == target
                and changed.assignment_revision == 2
                and changed.expires_at == original_expiry
            )
            with pytest.raises((ApprovalAuthorizationError, ApprovalConflict)):
                await decide(services, subject, reviewer, value=value, revision=2)
            reviewer = actor(services, target)
            await decide(services, subject, reviewer, value=value, revision=2)
        elif case == "schema":
            before = approval_snapshot(
                services, subject, invocation.invocation_id, include_owner=False
            )
            with pytest.raises(ApprovalValidationError):
                await decide(services, subject, reviewer, value="invalid response")
            assert (
                approval_snapshot(services, subject, invocation.invocation_id, include_owner=False)
                == before
            )
            assert services.approvals.store.get(subject).status == "pending" and business == []
            await decide(services, subject, reviewer, value=value)
        elif case == "null":
            value = None
            await decide(services, subject, reviewer, value=value)
        elif case == "race":
            duplicate = runtime(tmp_path, backend, business=business)
            second_actor = actor(duplicate, "reviewer-a")
            operations = [str(uuid4()), str(uuid4())]
            winners = await asyncio.gather(
                asyncio.to_thread(
                    lambda: asyncio.run(
                        decide(
                            services, subject, reviewer, "approved", value=value, key=operations[0]
                        )
                    )
                ),
                asyncio.to_thread(
                    lambda: asyncio.run(
                        decide(
                            duplicate,
                            subject,
                            second_actor,
                            "rejected",
                            value={"reason": "denied"},
                            key=operations[1],
                        )
                    )
                ),
                return_exceptions=True,
            )
            success = [record for record in winners if not isinstance(record, BaseException)]
            failures = [record for record in winners if isinstance(record, BaseException)]
            assert len(success) == len(failures) == 1 and isinstance(failures[0], ApprovalConflict)
            expected = success[0].status
            value = success[0].decision_event["data"]["value"]
            audit = services.approvals.protected_store.inspect(subject)[1][3]
            assert len(audit) == 1
        elif case == "reopen":
            await engine.shutdown()
            await asyncio.gather(task, return_exceptions=True)
            assert (
                services.invocations.get(invocation.invocation_id).status == "waiting"
                and not business
            )
            services.close()
            services = runtime(tmp_path, backend, business=business)
            engine = engine_type()
            await engine.initialize(services)
            invocation = services.invocations.get(invocation.invocation_id)
            reviewer = actor(services, "reviewer-a")
            restored = services.resolve_original_definition(invocation)
            assert (
                restored.fingerprint == plan.fingerprint
                and services.approvals.store.get(subject).expires_at == original_expiry
            )
            await decide(services, subject, reviewer, value=value)
            task = asyncio.create_task(engine.resume(invocation, {"replacement": True}, restored))
        else:
            await decide(services, subject, reviewer, value=value)
        result = await asyncio.wait_for(task, 30)
        assert result.status == "completed", result.error
        record = services.approvals.store.get(subject)
        assert record.status == expected and record.expires_at == original_expiry
        assert record.decision_event["data"]["value"] == value
        assert record.operator_id == (
            "system:approval-expiry" if expected == "expired" else reviewer.identity
        )
        assert len(business) == (1 if expected == "approved" else 0)
        if expected == "approved":
            assert business[0]["request"] == RAW and result.output == {
                "decision": expected,
                "value": value,
            }
        else:
            assert result.output[0]["decision"] == expected
        assert services.model.calls == [] and services.invocations.read_input(invocation) == RAW
        saved = identities(services, invocation.invocation_id)
        assert len([identity for identity in saved if identity.operation == REQUEST_POLICY]) == 1
        assert len([identity for identity in saved if identity.operation == MUTATION_POLICY]) == (
            2 if case in {"reassign", "escalate"} else 1
        )
        assert await engine.resume(invocation, {}, plan) == result
        assert len(business) == (1 if expected == "approved" else 0)
        encoded = json.dumps(record.as_dict())
        assert "fixture-token-" not in encoded
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.shutdown()
        services.close()
        if duplicate is not None:
            duplicate.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "damage", ["request-row", "request-policy-and-index", "audit", "input", "actor-profile"]
)
async def test_damaged_protected_approval_rejects_before_new_owner_native_or_business_mutation(
    tmp_path, engine_name, engine_type, backend, damage
):
    business = []
    services = runtime(tmp_path, backend, business=business)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    subject = str(uuid4())
    plan = compile_workflow(definition(subject, expiry()))
    invocation = handle(services, engine_name, plan)
    reached = asyncio.Event()
    original = engine.executor._run_listen

    async def after_selection(*args, **kwargs):
        selected = await original(*args, **kwargs)
        reached.set()
        await asyncio.Event().wait()
        return selected

    engine.executor._run_listen = after_selection
    task = asyncio.create_task(engine.invoke(plan, invocation, copy.deepcopy(RAW)))
    try:
        await waiting(services, invocation, task)
        await decide(services, subject, actor(services, "reviewer-a"), value=None)
        await asyncio.wait_for(reached.wait(), 30)
        assert not business
    finally:
        await engine.shutdown()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    connection = services.effect_journal.connection
    protected = services.approvals.protected_store
    if damage == "request-row":
        connection.execute("DELETE FROM " + protected.requests + " WHERE approval_id=?", (subject,))
    elif damage == "request-policy-and-index":
        identity = next(
            identity
            for identity in identities(services, invocation.invocation_id)
            if identity.operation == REQUEST_POLICY
        )
        connection.execute("DELETE FROM workflow_effects WHERE identity=?", (identity.key,))
        connection.execute(
            "DELETE FROM workflow_effect_invocations WHERE identity=?", (identity.key,)
        )
    elif damage == "audit":
        connection.execute(
            "UPDATE " + protected.audit + " SET digest=? WHERE approval_id=?", ("f" * 64, subject)
        )
    elif damage == "input":
        connection.execute(
            "UPDATE invocation_inputs SET payload=? WHERE invocation_id=?",
            ('{"changed":true}', invocation.invocation_id),
        )
    connection.commit()
    config = configuration(backend)
    if damage == "actor-profile":
        config = type(config).model_validate(
            {
                **config.model_dump(mode="json", exclude_unset=True),
                "security": {
                    "profiles": {
                        name: {
                            **profile.model_dump(mode="json"),
                            "principal": "retired-reviewer-a"
                            if name == "actor-a"
                            else profile.principal,
                        }
                        for name, profile in config.security.profiles.items()
                    }
                },
            }
        )
    services.close()
    recovered = runtime(tmp_path, backend, config=config, business=business)
    resumed = engine_type()
    await resumed.initialize(recovered)
    loaded = recovered.invocations.get(invocation.invocation_id)
    before = approval_snapshot(recovered, subject, loaded.invocation_id)
    native_before = native_snapshot(recovered, loaded, backend)
    try:
        with pytest.raises(OwaError):
            await asyncio.wait_for(resumed._preflight_timer_delivery(loaded), 30)
        assert approval_snapshot(recovered, subject, loaded.invocation_id) == before
        assert native_snapshot(recovered, loaded, backend) == native_before
        with pytest.raises(OwaError):
            await asyncio.wait_for(resumed.resume(loaded, {"replacement": True}, plan), 30)
        assert approval_snapshot(recovered, subject, loaded.invocation_id) == before
        assert native_snapshot(recovered, loaded, backend) == native_before
        assert not business and recovered.model.calls == []
    finally:
        await resumed.shutdown()
        recovered.close()
