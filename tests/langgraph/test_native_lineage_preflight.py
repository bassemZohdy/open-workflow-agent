"""Selected historical OWA bases validate only their completed scope closure."""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import uuid4

import pytest
from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.errors import OwaError
from open_workflow_agent.state_lineage import StateLineageRequest
from open_workflow_agent.storage import open_storage
from test_native_state_preflight import counts
from test_native_state_preflight import preflight as _preflight

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import native_lineage_preflight as module
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor
from open_workflow_agent_langgraph.native_lineage_preflight import prepare_native_lineage
from open_workflow_agent_langgraph.native_replay_coordinator import NativeReplayCoordinator
from open_workflow_agent_langgraph.native_state import state_reference

preflight = _preflight
pytestmark = pytest.mark.parametrize(
    "preflight", [("sqlite", "lineage"), ("postgresql", "lineage")], indirect=True
)


async def prepare(preflight, **changes):
    services, _engine, handle, actor, reference, *_ = preflight
    fields = dict(
        kind="replay",
        target_invocation_id=uuid4().hex,
        request_id=uuid4().hex * 2,
        source_reference=reference,
    )
    return await prepare_native_lineage(services, handle, actor, **(fields | changes))


@pytest.mark.asyncio
async def test_current_basis_has_completed_child_evidence_without_creating_any_business_run(
    preflight,
):
    services, engine, handle, actor, reference, *_ = preflight
    before = counts(preflight)
    lease = handle._execution_lease
    proposal = await prepare(preflight)
    assert proposal.request.source_reference == reference
    assert {item.reference for item in proposal.snapshots} == {"/do", "/do/1/nested/do"}
    assert all(item.occurrence == 1 for item in proposal.snapshots)
    assert StateLineageRequest.from_descriptor(proposal.request.descriptor) == proposal.request
    proposal.verify_basis(proposal.input_json, proposal.snapshots)
    root = proposal.snapshots[0]
    with pytest.raises(OwaError):
        proposal.verify_basis(
            proposal.input_json,
            (replace(root, checkpoint_id=str(uuid4())), *proposal.snapshots[1:]),
        )
    assert counts(preflight) == before and services.model.calls == []
    assert handle._execution_lease == lease
    assert "langgraph.state.replay@1.0.0" not in engine.capabilities().native_extensions


@pytest.mark.asyncio
async def test_unsupported_nested_timed_source_does_not_reserve_a_target(preflight):
    services, engine, handle, actor, reference, *_ = preflight
    coordinator = NativeReplayCoordinator(engine, NativeStateConfig())
    before = counts(preflight)
    reservations = coordinator.reservations.connection
    before_reservations = reservations.execute(
        "SELECT COUNT(*) FROM native_replay_reservations_v1"
    ).fetchone()[0]
    phases = []
    with pytest.raises(OwaError):
        await coordinator.run(
            handle,
            actor,
            request_id=uuid4().hex * 2,
            source_reference=reference,
            _phase=lambda name, target: phases.append((name, target)),
        )
    assert counts(preflight) == before
    assert (
        reservations.execute("SELECT COUNT(*) FROM native_replay_reservations_v1").fetchone()[0]
        == before_reservations
    )
    assert phases == [] and services.model.calls == []


@pytest.mark.asyncio
async def test_earlier_root_does_not_inherit_completed_scopes_from_later_progress(preflight):
    services, _engine, handle, _actor, _reference, _current, saver_type, plan = preflight
    path = services.engine_database_path("langgraph", create_namespace=False)
    root = LangGraphScopeExecutor(plan, None, handle.invocation_id).checkpoint_config("/do", 1)
    selected = None
    async with saver_type.from_conn_string(path) as saver:
        items = [item async for item in saver.alist(root)]
        for item in items:
            values = item.checkpoint["channel_values"]
            if "snapshot" in values:
                state = json.loads(values["snapshot"])
                if state["task_visits"] == {"/do/0/first": 1}:
                    selected = item
                    break
    assert selected is not None
    reference = state_reference(handle.invocation_id, plan.fingerprint, selected.checkpoint["id"])
    proposal = await prepare(preflight, source_reference=reference)
    assert len(proposal.snapshots) == 1
    assert (
        json.loads(proposal.snapshots[0].native_json)["snapshot"]
        == selected.checkpoint["channel_values"]["snapshot"]
    )


@pytest.mark.asyncio
async def test_initial_checkpoint_is_selectable_as_a_historical_replay_basis(preflight):
    services, _engine, handle, _actor, _, _, saver_type, plan = preflight
    root = LangGraphScopeExecutor(plan, None, handle.invocation_id).checkpoint_config("/do", 1)
    async with saver_type.from_conn_string(
        services.engine_database_path("langgraph", create_namespace=False)
    ) as saver:
        items = [item async for item in saver.alist(root)]
        initial = next(
            item for item in items if set(item.checkpoint["channel_values"]) == {"__start__"}
        )
    reference = state_reference(handle.invocation_id, plan.fingerprint, initial.checkpoint["id"])
    proposal = await prepare(preflight, source_reference=reference)
    assert len(proposal.snapshots) == 1 and proposal.snapshots[0].phase == "initial"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"target_invocation_id": "same"},
        {"source_reference": "0" * 64},
        {"acknowledge_reexecution": True},
        {"bounds": NativeStateConfig(max_native_bytes=1024)},
        {"bounds": NativeStateConfig(max_history=1, max_page_size=1)},
        {"kind": "branch", "task_reference": "/do/3/last", "values": {"value": 7}},
        {"kind": "branch", "task_reference": "/do/0/first", "values": {"value": "bad"}},
    ],
)
async def test_invalid_identity_base_schema_unvisited_attribution_and_budgets_fail_closed(
    preflight, changes
):
    if changes.get("target_invocation_id") == "same":
        changes = changes | {"target_invocation_id": preflight[2].invocation_id}
    before = counts(preflight)
    with pytest.raises(OwaError):
        await prepare(preflight, **changes)
    assert counts(preflight) == before and preflight[0].model.calls == []


@pytest.mark.asyncio
async def test_declared_data_branch_detaches_schema_valid_values_and_attribution(preflight):
    proposal = await prepare(
        preflight, kind="branch", task_reference="/do/0/first", values={"value": 7}
    )
    assert proposal.request.values == {"value": 7} and proposal.task_node is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["absent", "different", "policy"])
async def test_owner_and_current_permission_are_checked_before_native_io(
    preflight, monkeypatch, denial
):
    services, _engine, handle, actor, reference, *_ = preflight
    if denial == "absent":
        actor = None
    elif denial == "different":
        handle = replace(handle, owner_principal="other")
    else:
        services.config.execution_authorization.policy.rules[0].actions.remove("state.replay")
    attempts = []

    def forbidden(*args):
        attempts.append(True)
        raise AssertionError("native reader reached after failed authority")

    monkeypatch.setattr(module, "readonly_saver", forbidden)
    with pytest.raises(OwaError):
        await prepare_native_lineage(
            services,
            handle,
            actor,
            kind="replay",
            target_invocation_id=uuid4().hex,
            request_id=uuid4().hex * 2,
            source_reference=reference,
        )
    assert attempts == [] and services.model.calls == []


@pytest.mark.asyncio
async def test_an_existing_business_identity_cannot_be_repurposed(preflight):
    services, _engine, source, *_ = preflight
    target = services.invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        owner_principal="other",
        workflow_name=source.workflow_name,
        workflow_version=source.workflow_version,
        workflow_fingerprint=source.workflow_fingerprint,
    )
    before = counts(preflight)
    with pytest.raises(OwaError) as failure:
        await prepare(preflight, target_invocation_id=target.invocation_id)
    assert failure.value.details["reason"] == "new_business_identity_required"
    assert counts(preflight) == before


@pytest.mark.asyncio
async def test_missing_required_completed_scope_cannot_supply_a_lineage_basis(preflight):
    services, _engine, source, *_ = preflight
    proposal = await prepare(preflight)
    child = next(item for item in proposal.snapshots if item.reference != "/do")
    thread = LangGraphScopeExecutor.scope_thread_id(
        source.invocation_id, source.workflow_fingerprint, child.reference, child.occurrence
    )
    connection = open_storage(
        services.engine_database_path("langgraph", create_namespace=False), "owa_langgraph"
    )
    try:
        connection.execute("DELETE FROM checkpoints WHERE thread_id=?", (thread,))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(OwaError):
        await prepare(preflight)
