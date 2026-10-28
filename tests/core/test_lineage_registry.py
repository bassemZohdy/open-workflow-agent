"""Common lineage bindings preserve original receipt evidence and effect policy."""

from contextlib import suppress
from dataclasses import replace
from uuid import uuid4

import pytest
from open_workflow_agent.effects import EffectJournal, ReceiptDependency, effect_request_digest
from open_workflow_agent.errors import InvocationStateConflict, OwaError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.execution_authorization import ExecutionAuthorizationStore
from open_workflow_agent.execution_context import ExecutionActionContext, action_scope
from open_workflow_agent.lineage_registry import LineageRegistry, lineage_identity, lineage_scope
from open_workflow_agent.persistence import InvocationStore
from open_workflow_agent.recovery_definitions import RecoveryDefinitionArchive
from open_workflow_agent.state_lineage import StateLineageRequest
from test_execution_authorization import _admit, _decision, _finish
from test_execution_authorization import authority_tree as _authority_tree

authority_tree = _authority_tree


@pytest.fixture
def lineage(authority_tree):
    services, archive, auth, plan, source, actor, effect = authority_tree
    rule = services.config.execution_authorization.policy.rules[0]
    rule.actions.extend(["state.replay", "state.branch"])
    rule.resources.append("*")
    _admit(authority_tree, {"original": True}, reserved=True)
    _decision(authority_tree)
    receipt = _finish(authority_tree, {"answer": 42})
    target = services.invocations.create(
        engine=source.engine,
        session_id=source.session_id,
        user_id=source.user_id,
        owner_principal=source.owner_principal,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    services._authority_fixture_invocations.append(target.invocation_id)
    lease = services.execution_ownership.acquire(target, seconds=300)
    target._execution_lease = lease
    auth.reserve_invocation(target, actor, input_data={"original": True})
    with authority_scope(actor):
        archive.admit(plan, target, {"original": True}, execution_lease=lease)
    request = StateLineageRequest(
        source.invocation_id,
        target.invocation_id,
        plan.fingerprint,
        uuid4().hex * 2,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        "replay",
        "read_only",
        False,
    )
    registry = LineageRegistry(
        services.effect_journal,
        services.invocations,
        auth,
        lambda _request, digest: digest == "e" * 64,
    )
    dependency = ReceiptDependency.capture(effect, receipt)
    try:
        yield services, registry, source, target, actor, request, dependency
    finally:
        with suppress(InvocationStateConflict):
            services.execution_ownership.release(lease)


def test_binding_maps_source_receipt_without_creating_a_target_business_receipt(lineage):
    services, registry, source, target, actor, request, dependency = lineage
    first = registry.bind(request, target, actor, "e" * 64, (dependency,))
    assert registry.bind(request, target, actor, "e" * 64, (dependency,)) == first
    loaded, mapped, proof = registry.read(target)
    assert loaded == request and proof == "e" * 64 and len(mapped) == 1
    assert mapped[0].source == dependency and mapped[0].target.invocation_id == target.invocation_id
    assert mapped[0].target.task_reference == dependency.identity.task_reference
    assert services.effect_journal.get(mapped[0].target) is None
    assert services.effect_journal.get(dependency.identity) is not None
    with pytest.raises(OwaError) as denied:
        registry.authorize_effect(target, mapped[0].target)
    assert denied.value.details["reason"] == "read_only_effect_denied"
    assert source.invocation_id != target.invocation_id


def test_reexecution_requires_acknowledgement_and_new_target_identity(lineage):
    _, registry, _, target, actor, request, dependency = lineage
    request = replace(request, effect_policy="reexecute", acknowledge_reexecution=True)
    registry.bind(request, target, actor, "e" * 64, (dependency,))
    mapping = registry.read(target)[1][0]
    registry.authorize_effect(target, mapping.target)
    with pytest.raises(OwaError):
        registry.authorize_effect(target, dependency.identity)
    with pytest.raises(OwaError):
        replace(request, acknowledge_reexecution=False)


def test_changed_receipt_invalidates_read_and_effect_permission(lineage):
    services, registry, _, target, actor, request, dependency = lineage
    registry.bind(request, target, actor, "e" * 64, (dependency,))
    connection = services.effect_journal.connection
    connection.execute("DELETE FROM workflow_effects WHERE identity=?", (dependency.identity.key,))
    connection.commit()
    with pytest.raises(OwaError):
        registry.read(target)
    with pytest.raises(OwaError):
        registry.authorize_effect(
            target, replace(dependency.identity, invocation_id=target.invocation_id)
        )


def test_absent_or_conflicting_binding_fails_closed(lineage):
    _, registry, _, target, actor, request, dependency = lineage
    with pytest.raises(OwaError):
        registry.authorize_effect(
            target, replace(dependency.identity, invocation_id=target.invocation_id)
        )
    registry.bind(request, target, actor, "e" * 64, (dependency,))
    with pytest.raises(OwaError):
        registry.bind(request, target, actor, "f" * 64, (dependency,))
    with pytest.raises(OwaError):
        registry.bind(request, target, actor, "e" * 64, (dependency, dependency))
    assert registry.journal.get(lineage_identity(target)) is not None


@pytest.mark.asyncio
async def test_action_authorization_blocks_read_only_before_any_business_intent(lineage):
    services, registry, _, target, actor, request, dependency = lineage
    registry.bind(request, target, actor, "e" * 64, (dependency,))
    identity = replace(dependency.identity, invocation_id=target.invocation_id)
    with lineage_scope(registry), action_scope(ExecutionActionContext(target, identity)):
        with pytest.raises(OwaError) as denied:
            await services.authorize_current_action(
                "protocol.call", "http:https://peer.test/action"
            )
    assert denied.value.details["reason"] == "read_only_effect_denied"
    assert services.effect_journal.get(identity) is None


def test_missing_native_proof_blocks_existing_binding_and_effects(lineage):
    _, registry, _, target, actor, request, dependency = lineage
    registry.bind(request, target, actor, "e" * 64, (dependency,))
    registry.native_proof_verifier = lambda *_: False
    with pytest.raises(OwaError) as failure:
        registry.authorize_effect(
            target, replace(dependency.identity, invocation_id=target.invocation_id)
        )
    assert failure.value.details["reason"] == "native_proof_unavailable"


@pytest.mark.asyncio
async def test_reexecution_uses_new_authorized_receipt_identity(lineage):
    services, registry, _, target, actor, request, dependency = lineage
    request = replace(request, effect_policy="reexecute", acknowledge_reexecution=True)
    registry.bind(request, target, actor, "e" * 64, (dependency,))
    identity = registry.read(target)[1][0].target
    assert services.effect_journal.get(identity) is None
    with lineage_scope(registry), action_scope(ExecutionActionContext(target, identity)):
        await services.authorize_current_action("protocol.call", "http:https://peer.test/action")
        services.effect_journal.begin(
            identity,
            effect_request_digest("http", {"new": True}),
            "replay",
            execution_lease=target._execution_lease,
        )
        completed = services.effect_journal.finish_owned(
            identity, "replay", output={"new": True}, execution_lease=target._execution_lease
        )
    assert completed.status == "completed"
    assert identity.key != dependency.identity.key
    assert services.effect_journal.get(dependency.identity) is not None


def test_reopen_recovers_binding_and_verifies_source_receipt(lineage):
    services, registry, _, target, actor, request, dependency = lineage
    registry.bind(request, target, actor, "e" * 64, (dependency,))
    database = services.execution_ownership._database
    invocations = InvocationStore(database)
    journal = EffectJournal(database, invocations=invocations)
    archive = RecoveryDefinitionArchive(
        journal, invocations, profile_provider=registry.authorizations.archive.profile_provider
    )
    authorizations = ExecutionAuthorizationStore(
        journal,
        invocations,
        archive,
        config_provider=lambda: services.config.execution_authorization,
        security_provider=lambda: services.config.security,
    )
    reopened = LineageRegistry(
        journal, invocations, authorizations, lambda _request, digest: digest == "e" * 64
    )
    try:
        recovered, mapped, proof = reopened.read(target)
        assert recovered == request and mapped[0].source == dependency and proof == "e" * 64
    finally:
        journal.close()
        invocations.close()


def test_lost_binding_cannot_be_replaced_when_its_index_remains(lineage):
    services, registry, _, target, actor, request, dependency = lineage
    registry.bind(request, target, actor, "e" * 64, (dependency,))
    connection = services.effect_journal.connection
    connection.execute(
        "DELETE FROM workflow_effects WHERE identity=?", (lineage_identity(target).key,)
    )
    connection.commit()
    with pytest.raises(OwaError) as failure:
        registry.bind(request, target, actor, "e" * 64, (dependency,))
    assert failure.value.details["reason"] == "lineage_binding_orphaned"


def test_expired_target_owner_cannot_authorize_reexecution(lineage):
    services, registry, _, target, actor, request, dependency = lineage
    request = replace(request, effect_policy="reexecute", acknowledge_reexecution=True)
    registry.bind(request, target, actor, "e" * 64, (dependency,))
    connection = services.execution_ownership.connection
    connection.execute(
        "UPDATE invocation_ownership SET expires_at=1 WHERE invocation_id=?",
        (target.invocation_id,),
    )
    connection.commit()
    with pytest.raises(OwaError) as failure:
        registry.authorize_effect(
            target, replace(dependency.identity, invocation_id=target.invocation_id)
        )
    assert failure.value.details["reason"] == "effect_ownership_lost"


@pytest.mark.asyncio
async def test_lineage_scope_rejects_actions_without_invocation_context(lineage):
    services, registry, *_ = lineage
    with lineage_scope(registry), pytest.raises(OwaError):
        await services.authorize_current_action("model.complete", "fake")
