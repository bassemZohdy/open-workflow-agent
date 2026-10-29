"""Private native replay from a real historical checkpoint and common lineage."""

import json
import os
from uuid import uuid4

import pytest
from native_stream_test_support import authority, configuration
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.lineage_registry import LineageRegistry
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import (
    ExecutionState,
    WorkflowExecutor,
    compile_workflow,
    generate_default_workflow,
)

pytest.importorskip("langgraph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine, native
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor, _node_name
from open_workflow_agent_langgraph.native_lineage_fork import (
    apply_native_lineage_fork,
    prepare_lineage_storage,
)
from open_workflow_agent_langgraph.native_lineage_inspection import inspect_native_lineage
from open_workflow_agent_langgraph.native_lineage_preflight import prepare_native_lineage
from open_workflow_agent_langgraph.native_lineage_transition import stage_root_replay
from open_workflow_agent_langgraph.native_state import state_reference


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("policy", ["read_only", "reexecute"])
@pytest.mark.parametrize("continuation", ["direct", "restarted"])
async def test_historical_replay_binds_original_receipt_and_enforces_effect_policy(
    tmp_path, backend, policy, continuation
):
    config = configuration()
    config.observability.native_streams = None
    config.execution_authorization.policy.rules[0].actions.append("state.replay")
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("PostgreSQL datasource required")
        config.persistence.datasource = datasource
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if backend == "postgresql" else tmp_path
    )
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    source = generate_default_workflow()
    source["do"] = [
        {"first": {"call": "agent:1.0.0@default"}},
        {"second": {"call": "agent:1.0.0@default"}},
    ]
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [{"name": "langgraph.state.replay", "version": "1.0.0"}],
            "nativeExtensionData": {
                "langgraph.state.replay@1.0.0": {
                    "authorizationPolicyRef": config.execution_authorization.id,
                    "replayEffectPolicy": policy,
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
    target = None
    try:
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
        assert len(services.model.calls) == 2
        database = services.engine_database_path("langgraph", create_namespace=False)
        saver_type = (
            native.AsyncPostgresSaver if backend == "postgresql" else native.AsyncSqliteSaver
        )
        root = LangGraphScopeExecutor(plan, None, parent.invocation_id).checkpoint_config("/do", 1)
        async with saver_type.from_conn_string(database) as saver:
            await prepare_lineage_storage(saver, postgresql=backend == "postgresql")
            checkpoints = [item async for item in saver.alist(root)]
        selected = next(
            item
            for item in checkpoints
            if "snapshot" in item.checkpoint["channel_values"]
            and json.loads(item.checkpoint["channel_values"]["snapshot"])["task_visits"]
            == {"/do/0/first": 1}
        )
        reference = state_reference(
            parent.invocation_id, plan.fingerprint, selected.checkpoint["id"]
        )
        reserved = str(uuid4())
        prepared = await prepare_native_lineage(
            services,
            parent,
            actor,
            kind="replay",
            target_invocation_id=reserved,
            request_id=uuid4().hex * 2,
            source_reference=reference,
            acknowledge_reexecution=policy == "reexecute",
            bounds=NativeStateConfig(timeout_seconds=30),
        )
        target = services.invocations.create_in_transaction(
            services.invocations.connection,
            engine="langgraph",
            session_id=parent.session_id,
            user_id=parent.user_id,
            owner_principal="alice",
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
            input_data={},
            reserved_invocation_id=reserved,
        )
        services.invocations.connection.commit()
        target._execution_lease = services.execution_ownership.acquire(target, seconds=300)
        services.execution_authorizations.reserve_invocation(target, actor, input_data={})
        with authority_scope(actor):
            async with services.recovery_definitions.owned_profile("langgraph"):
                services.recovery_definitions.admit(
                    plan, target, {}, execution_lease=target._execution_lease
                )
        proposal, dependencies = stage_root_replay(services, plan, prepared, target)
        assert len(dependencies) == 1 and dependencies[0].identity.task_reference == "/do/0/first"

        async def write(saver, configs):
            driver = LangGraphScopeExecutor(plan, saver, target.invocation_id)
            graph = driver.compile_scope(
                plan.scopes[0],
                WorkflowExecutor(services.catalog, services=services),
                ExecutionState(data=None),
            )
            result = await graph.aupdate_state(
                configs[0],
                json.loads(proposal.packets[0].native_json),
                as_node=_node_name(plan.scopes[0].tasks[0]),
            )
            return (result,)

        await apply_native_lineage_fork(
            database,
            services.execution_ownership.native_fence(target._execution_lease),
            target,
            plan,
            proposal,
            write,
            bounds=NativeStateConfig(timeout_seconds=30),
        )
        bounds = NativeStateConfig(timeout_seconds=30)
        digest = inspect_native_lineage(database, prepared.request, bounds)
        assert digest is not None
        registry = LineageRegistry(
            services.effect_journal,
            services.invocations,
            services.execution_authorizations,
            lambda request, value: inspect_native_lineage(database, request, bounds) == value,
        )
        registry.bind(prepared.request, target, actor, digest, dependencies)
        # Recovery admission must reconstruct evidence from storage even before
        # the new worker acquires the target's execution lease.
        reopened = RuntimeServices(
            config, model=FakeModel(), database_root=None if backend == "postgresql" else tmp_path
        )
        restarted = LangGraphWorkflowEngine()
        try:
            await restarted.initialize(reopened)
            await restarted._timer_dispatcher.stop()
            recovered = reopened.invocations.get(target.invocation_id)
            assert recovered is not None and recovered._execution_lease is None
            recovered_registry = restarted._resume_lineage(recovered)
            assert recovered_registry is not None
            await restarted.native.admit_resume(
                plan,
                recovered.invocation_id,
                {},
                effect_journal=reopened.effect_journal,
                lineage_registry=recovered_registry,
                lineage_target=recovered,
            )
            assert reopened.model.calls == []
            if continuation == "restarted":
                services.execution_ownership.release(target._execution_lease)
                target._execution_lease = None
                async with restarted._own_execution(recovered):
                    result = await restarted._resume_owned(recovered, None, plan)
                    assert result.status == ("faulted" if policy == "read_only" else "completed")
                    assert len(reopened.model.calls) == (0 if policy == "read_only" else 1)
                    repeat = await restarted._resume_owned(recovered, None, plan)
                    assert repeat.status == result.status
                    assert len(reopened.model.calls) == (0 if policy == "read_only" else 1)
                return
        finally:
            await restarted.shutdown()
            reopened.close()
        metadata = {
            "invocation_id": target.invocation_id,
            "_invocation_handle": target,
            "authority": actor.as_value(),
            "engine": "langgraph",
            "_lineage_registry": registry,
        }

        async def resume():
            with authority_scope(actor):
                return await engine.native.execute_plan(
                    engine.executor,
                    plan,
                    {},
                    metadata=metadata,
                    invocation_id=target.invocation_id,
                    resume=True,
                    _execution_fence=services.execution_ownership.native_fence(
                        target._execution_lease
                    ),
                )

        # A checkpoint carrying remapped receipts must never resume through the
        # ordinary path without the verified lineage context.
        metadata.pop("_lineage_registry")
        with pytest.raises(OwaError):
            await resume()
        assert len(services.model.calls) == 2
        # Reconstructing the verifier uses only the persisted native and common
        # evidence, as a newly initialized runtime would have to do.
        metadata["_lineage_registry"] = LineageRegistry(
            services.effect_journal,
            services.invocations,
            services.execution_authorizations,
            lambda request, value: inspect_native_lineage(database, request, bounds) == value,
        )

        if policy == "read_only":
            with pytest.raises(OwaError):
                await resume()
            assert len(services.model.calls) == 2
        else:
            result = await resume()
            assert len(services.model.calls) == 3
            assert result is not None
            await resume()
            assert len(services.model.calls) == 3
    finally:
        if target is not None and target._execution_lease is not None:
            services.execution_ownership.release(target._execution_lease)
        services.execution_ownership.release(parent._execution_lease)
        await engine.shutdown()
        services.close()
