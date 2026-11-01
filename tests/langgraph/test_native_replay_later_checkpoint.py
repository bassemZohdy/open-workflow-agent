"""Private root replay from a later historical checkpoint retains earlier receipts."""

import json
from uuid import uuid4

import pytest
from native_stream_test_support import authority, configuration
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine, native
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor
from open_workflow_agent_langgraph.native_lineage_fork import prepare_lineage_storage
from open_workflow_agent_langgraph.native_replay_coordinator import NativeReplayCoordinator
from open_workflow_agent_langgraph.native_state import state_reference

TASKS = ("first", "second", "third")


@pytest.mark.asyncio
@pytest.mark.parametrize("completed", [1, 2])
async def test_replay_from_later_root_checkpoint_reexecutes_only_remaining_tasks(
    tmp_path, completed
):
    config = configuration()
    config.observability.native_streams = None
    config.execution_authorization.policy.rules[0].actions.append("state.replay")
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    source = generate_default_workflow()
    source["do"] = [{name: {"call": "agent:1.0.0@default"}} for name in TASKS]
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [{"name": "langgraph.state.replay", "version": "1.0.0"}],
            "nativeExtensionData": {
                "langgraph.state.replay@1.0.0": {
                    "authorizationPolicyRef": config.execution_authorization.id,
                    "replayEffectPolicy": "reexecute",
                }
            },
        }
    }
    plan = compile_workflow(source)
    actor = authority(services, "alice")
    parent = services.invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    parent._execution_lease = services.execution_ownership.acquire(parent, seconds=300)
    services.execution_authorizations.reserve_invocation(parent, actor, input_data={})
    with authority_scope(actor):
        async with services.recovery_definitions.owned_profile("langgraph"):
            services.recovery_definitions.admit(
                plan, parent, {}, execution_lease=parent._execution_lease
            )
        await engine.native.execute_plan(
            engine.executor,
            plan,
            {},
            metadata={
                "invocation_id": parent.invocation_id,
                "_invocation_handle": parent,
                "authority": actor.as_value(),
                "engine": "langgraph",
            },
            invocation_id=parent.invocation_id,
            _execution_fence=services.execution_ownership.native_fence(parent._execution_lease),
        )
    assert len(services.model.calls) == len(TASKS)
    database = services.engine_database_path("langgraph", create_namespace=False)
    root = LangGraphScopeExecutor(plan, None, parent.invocation_id).checkpoint_config("/do", 1)
    wanted = {f"/do/{index}/{name}": 1 for index, name in enumerate(TASKS[:completed])}
    async with native.AsyncSqliteSaver.from_conn_string(database) as saver:
        await prepare_lineage_storage(saver, postgresql=False)
        checkpoints = [item async for item in saver.alist(root)]
    selected = next(
        item
        for item in checkpoints
        if "snapshot" in item.checkpoint["channel_values"]
        and json.loads(item.checkpoint["channel_values"]["snapshot"])["task_visits"] == wanted
    )
    reference = state_reference(parent.invocation_id, plan.fingerprint, selected.checkpoint["id"])
    coordinator = NativeReplayCoordinator(engine, NativeStateConfig(timeout_seconds=30))
    options = {
        "request_id": uuid4().hex * 2,
        "source_reference": reference,
        "acknowledge_reexecution": True,
    }
    result = await coordinator.run(parent, actor, **options)
    assert result.status == "completed"
    assert len(services.model.calls) == len(TASKS) + (len(TASKS) - completed)
    repeated = await coordinator.run(parent, actor, **options)
    assert repeated.status == "completed"
    assert len(services.model.calls) == len(TASKS) + (len(TASKS) - completed)
