"""Real SQLite/PostgreSQL fork evidence must match retained SDK projections."""

from dataclasses import replace

import pytest
from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.execution_context import ExecutionActionContext, action_scope
from open_workflow_agent.lineage_registry import LineageRegistry, lineage_scope
from open_workflow_agent.storage import open_storage
from test_native_lineage_fork import apply
from test_native_lineage_fork import fork as _fork
from test_native_state_preflight import preflight as _preflight

pytest.importorskip("langgraph")
from open_workflow_agent_langgraph.native_lineage_fork import TABLE
from open_workflow_agent_langgraph.native_lineage_inspection import (
    inspect_native_lineage,
    selected_business_receipts,
)

fork = _fork
preflight = _preflight
pytestmark = pytest.mark.parametrize(
    "preflight", [("sqlite", "lineage"), ("postgresql", "lineage")], indirect=True
)


@pytest.mark.asyncio
async def test_committed_fork_has_a_stable_private_proof_digest(fork):
    request, database = fork[7].preflight.request, fork[5]
    bounds = NativeStateConfig(timeout_seconds=30)
    assert inspect_native_lineage(database, request, bounds) is None
    await apply(fork)
    digest = inspect_native_lineage(database, request, bounds)
    assert isinstance(digest, str) and len(digest) == 64
    assert inspect_native_lineage(database, request, bounds) == digest
    with pytest.raises(OwaError):
        inspect_native_lineage(database, replace(request, storage_identity="0" * 64), bounds)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["packet_digest", "native_checkpoint", "proof_oversized"])
async def test_changed_or_missing_native_evidence_is_never_accepted(fork, damage):
    request, database = fork[7].preflight.request, fork[5]
    bounds = NativeStateConfig(timeout_seconds=30)
    proof = await apply(fork)
    assert inspect_native_lineage(database, request, bounds) is not None
    connection = open_storage(database, "owa_langgraph")
    try:
        if damage == "packet_digest":
            connection.execute(
                f"UPDATE {TABLE} SET packet_digest=? WHERE identity=?",
                ("a" * 64, request.identity.key),
            )
        elif damage == "proof_oversized":
            connection.execute(
                f"UPDATE {TABLE} SET result_json=? WHERE identity=?",
                ("x" * 70_000, request.identity.key),
            )
        else:
            item = proof.checkpoints[0]
            from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

            thread = LangGraphScopeExecutor.scope_thread_id(
                request.target_invocation_id,
                request.workflow_fingerprint,
                item.reference,
                item.occurrence,
            )
            connection.execute(
                "DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_id=?",
                (thread, item.checkpoint_id),
            )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(OwaError):
        inspect_native_lineage(database, request, bounds)


@pytest.mark.asyncio
async def test_real_fork_evidence_binds_common_lineage_and_blocks_read_only_action(fork):
    services, _engine, _source, target, plan, database, _fence, proposal, _saver, preflight = fork
    actor = preflight[3]
    services.execution_authorizations.reserve_invocation(target, actor, input_data={})
    with authority_scope(actor):
        async with services.recovery_definitions.owned_profile("langgraph"):
            services.recovery_definitions.admit(
                plan, target, {}, execution_lease=target._execution_lease
            )
    await apply(fork)
    bounds = NativeStateConfig(timeout_seconds=30)
    request = proposal.preflight.request
    proof = inspect_native_lineage(database, request, bounds)
    assert proof is not None
    registry = LineageRegistry(
        services.effect_journal,
        services.invocations,
        services.execution_authorizations,
        lambda command, digest: inspect_native_lineage(database, command, bounds) == digest,
    )
    dependencies = selected_business_receipts(proposal.preflight, services.effect_journal)
    registry.bind(request, target, actor, proof, dependencies)
    identity = EffectIdentity(
        target.invocation_id, plan.fingerprint, "/do/0/first", (("/do/0/first", 1),), "call:agent"
    )
    with lineage_scope(registry), action_scope(ExecutionActionContext(target, identity)):
        with pytest.raises(OwaError) as failure:
            await services.authorize_current_action("catalog.call", "agent:1.0.0@default")
    assert failure.value.details["reason"] == "read_only_effect_denied"
    assert services.effect_journal.get(identity) is None and services.model.calls == []
