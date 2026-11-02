"""Private root branch projection with a real native fork and continuation."""

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from native_stream_test_support import authority, configuration
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.lineage_registry import LineageRegistry
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import ExecutionState, compile_workflow, generate_default_workflow

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine, native
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor
from open_workflow_agent_langgraph.native_lineage_fork import (
    apply_native_lineage_fork,
    prepare_lineage_storage,
)
from open_workflow_agent_langgraph.native_lineage_inspection import inspect_native_lineage
from open_workflow_agent_langgraph.native_lineage_preflight import prepare_native_lineage
from open_workflow_agent_langgraph.native_lineage_reservation import (
    NativeBranchReservations,
    NativeReplayReservations,
)
from open_workflow_agent_langgraph.native_lineage_transition import stage_root_branch
from open_workflow_agent_langgraph.native_state import state_reference


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("policy", ["read_only", "reexecute"])
@pytest.mark.parametrize("downstream_effect", [False, True])
async def test_private_branch_replaces_data_at_last_completed_task_and_preserves_source(
    tmp_path, monkeypatch, backend, policy, downstream_effect
):
    config = configuration()
    config.observability.native_streams = None
    config.execution_authorization.policy.rules[0].actions.extend(["state.branch", "state.replay"])
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("PostgreSQL datasource required")
        config.persistence.datasource = datasource
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if backend == "postgresql" else tmp_path
    )
    # Direct native execution bypasses the normal invocation's namespace setup.
    services.engine_database_path("langgraph")
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    source = generate_default_workflow()
    source["do"] = [
        {"first": {"set": {"value": 1}}},
        {"effect": {"call": "agent:1.0.0@default"}},
        {"second": {"set": {"value": 2}}},
        {"third": {"set": {"value": "${ .value + 1 }"}}},
    ]
    if downstream_effect:
        source["do"].append({"after": {"call": "agent:1.0.0@default"}})
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {"name": "langgraph.state.update", "version": "1.0.0"},
                {"name": "langgraph.state.branch", "version": "1.0.0"},
                {"name": "langgraph.state.replay", "version": "1.0.0"},
            ],
            "nativeExtensionData": {
                "langgraph.state.update@1.0.0": {
                    "allowedTaskRefs": ["/do/0", "/do/2"],
                    "updateSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                    "authorizationPolicyRef": config.execution_authorization.id,
                },
                "langgraph.state.branch@1.0.0": {
                    "authorizationPolicyRef": config.execution_authorization.id,
                    "branchEffectPolicy": policy,
                },
                "langgraph.state.replay@1.0.0": {
                    "authorizationPolicyRef": config.execution_authorization.id,
                    "replayEffectPolicy": policy,
                },
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
            original = await engine.native.execute_plan(
                engine.executor,
                plan,
                {},
                invocation_id=parent.invocation_id,
                metadata={
                    "invocation_id": parent.invocation_id,
                    "_invocation_handle": parent,
                    "authority": actor.as_value(),
                    "engine": "langgraph",
                },
                _execution_fence=services.execution_ownership.native_fence(parent._execution_lease),
            )
        if not downstream_effect:
            assert original == {"value": 3}
        assert len(services.model.calls) == (2 if downstream_effect else 1)
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
            == {"/do/0/first": 1, "/do/1/effect": 1, "/do/2/second": 1}
        )
        reference = state_reference(
            parent.invocation_id, plan.fingerprint, selected.checkpoint["id"]
        )
        target_id = str(uuid4())
        options = dict(
            kind="branch",
            target_invocation_id=target_id,
            request_id=uuid4().hex * 2,
            source_reference=reference,
            acknowledge_reexecution=policy == "reexecute",
            task_reference="/do/2/second",
            values={"value": 7},
        )
        prepared = await prepare_native_lineage(services, parent, actor, **options)
        store = NativeBranchReservations(services)
        insert = services.invocations.create_in_transaction

        def interrupted(*args, **kwargs):
            insert(*args, **kwargs)
            raise RuntimeError("simulated interruption")

        with monkeypatch.context() as patch:
            patch.setattr(services.invocations, "create_in_transaction", interrupted)
            with pytest.raises(RuntimeError, match="simulated interruption"):
                store.reserve(parent, actor, plan, prepared)
        assert services.invocations.get(target_id) is None
        assert store._row(prepared.request.identity.key) is None
        if not downstream_effect and policy == "read_only":
            environment = os.environ.copy()
            if backend == "sqlite":
                environment.pop("OWA_TEST_POSTGRES_URL", None)
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("native_branch_reservation_worker.py")),
                    str(tmp_path),
                    parent.invocation_id,
                    target_id,
                    options["request_id"],
                    reference,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
            )
            output = []
            try:

                async def read_marker():
                    while True:
                        line = await asyncio.to_thread(process.stdout.readline)
                        if not line:
                            raise AssertionError(
                                "worker exited before reservation: " + "".join(output)
                            )
                        output.append(line)
                        if line.startswith("BRANCH_RESERVED "):
                            return line.strip()

                assert (
                    await asyncio.wait_for(read_marker(), timeout=120)
                    == "BRANCH_RESERVED " + target_id
                )
                process.kill()
                await asyncio.to_thread(process.wait, timeout=10)
            finally:
                if process.poll() is None:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=10)
                process.stdout.close()
            _, target = store.recover(parent, actor, prepared.request.request_id)
        else:
            target = store.reserve(parent, actor, plan, prepared)
        assert store.reserve(parent, actor, plan, prepared).invocation_id == target_id
        retained, recovered = store.recover(parent, actor, prepared.request.request_id)
        assert retained == prepared.request and recovered.invocation_id == target_id
        assert (
            await prepare_native_lineage(
                services, parent, actor, **options, _reserved_request=retained
            )
            == prepared
        )
        reopened = RuntimeServices(
            config, model=FakeModel(), database_root=None if backend == "postgresql" else tmp_path
        )
        restarted = LangGraphWorkflowEngine()
        try:
            await restarted.initialize(reopened)
            await restarted._timer_dispatcher.stop()
            reopened_actor = authority(reopened, "alice")
            reopened_parent = reopened.invocations.get(parent.invocation_id)
            reopened_request, reopened_target = NativeBranchReservations(reopened).recover(
                reopened_parent, reopened_actor, prepared.request.request_id
            )
            assert reopened_target.invocation_id == target_id
            assert reopened_request == prepared.request
            assert (
                await prepare_native_lineage(
                    reopened,
                    reopened_parent,
                    reopened_actor,
                    **options,
                    _reserved_request=reopened_request,
                )
                == prepared
            )
        finally:
            await restarted.shutdown()
            reopened.close()
        changed = replace(prepared, request=replace(prepared.request, values_json='{"value":8}'))
        with pytest.raises(OwaError):
            store.reserve(parent, actor, plan, changed)
        with pytest.raises(OwaError):
            NativeReplayReservations(services).recover(parent, actor, prepared.request.request_id)
        if not downstream_effect and policy == "read_only":
            replay_store = NativeReplayReservations(services)
            replay_options = {
                "kind": "replay",
                "target_invocation_id": str(uuid4()),
                "request_id": prepared.request.request_id,
                "source_reference": reference,
            }
            collision = await prepare_native_lineage(services, parent, actor, **replay_options)
            with pytest.raises(OwaError):
                replay_store.reserve(parent, actor, plan, collision)
            assert services.invocations.get(replay_options["target_invocation_id"]) is None
            replay_options["request_id"] = uuid4().hex * 2
            independent = await prepare_native_lineage(services, parent, actor, **replay_options)
            replay_store.reserve(parent, actor, plan, independent, max_per_source=1)
            assert store._source_count(store.connection, parent.invocation_id) == 1
            assert replay_store._source_count(store.connection, parent.invocation_id) == 1
            assert (
                store.reserve(parent, actor, plan, prepared, max_per_source=1).invocation_id
                == target_id
            )
        target._execution_lease = services.execution_ownership.acquire(target, seconds=300)
        services.execution_authorizations.reserve_invocation(target, actor, input_data={})
        with authority_scope(actor):
            async with services.recovery_definitions.owned_profile("langgraph"):
                services.recovery_definitions.admit(
                    plan, target, {}, execution_lease=target._execution_lease
                )
        # Attribution must match the latest completed node, not merely any visited node.
        earlier = await prepare_native_lineage(
            services,
            parent,
            actor,
            **(options | {"target_invocation_id": str(uuid4()), "task_reference": "/do/0/first"}),
        )
        earlier = replace(earlier, request=replace(earlier.request, target_invocation_id=target_id))
        with pytest.raises(OwaError):
            stage_root_branch(services, plan, earlier, target)
        with pytest.raises(OwaError):
            store.reserve(parent, actor, plan, earlier)
        with pytest.raises(OwaError):
            stage_root_branch(services, plan, replace(prepared, task_node="wrong"), target)
        other_policy = "reexecute" if policy == "read_only" else "read_only"
        changed_policy = replace(
            prepared.request,
            effect_policy=other_policy,
            acknowledge_reexecution=other_policy == "reexecute",
        )
        with pytest.raises(OwaError):
            stage_root_branch(services, plan, replace(prepared, request=changed_policy), target)
        invalid_values = replace(prepared.request, values_json='{"value":"bad"}')
        with pytest.raises(OwaError):
            stage_root_branch(services, plan, replace(prepared, request=invalid_values), target)
        proposal, dependencies = stage_root_branch(services, plan, prepared, target)
        assert len(dependencies) == 1
        assert dependencies[0].identity.invocation_id == parent.invocation_id
        snapshot = json.loads(json.loads(proposal.packets[0].native_json)["snapshot"])
        assert snapshot["data"] == {"value": 7}
        assert snapshot["variables"]["workflow"]["id"] == target_id

        async def write(saver, configs):
            graph = LangGraphScopeExecutor(plan, saver, target_id).compile_scope(
                plan.scopes[0], engine.executor, ExecutionState(data=None)
            )
            result = await graph.aupdate_state(
                configs[0], json.loads(proposal.packets[0].native_json), as_node=prepared.task_node
            )
            return (result,)

        bounds = NativeStateConfig(timeout_seconds=30)
        await apply_native_lineage_fork(
            database,
            services.execution_ownership.native_fence(target._execution_lease),
            target,
            plan,
            proposal,
            write,
            bounds=bounds,
        )
        digest = inspect_native_lineage(database, prepared.request, bounds)
        assert digest is not None
        registry = LineageRegistry(
            services.effect_journal,
            services.invocations,
            services.execution_authorizations,
            lambda request, value: inspect_native_lineage(database, request, bounds) == value,
        )
        registry.bind(prepared.request, target, actor, digest, dependencies)

        async def resume():
            with authority_scope(actor):
                return await engine.native.execute_plan(
                    engine.executor,
                    plan,
                    {},
                    invocation_id=target_id,
                    resume=True,
                    metadata={
                        "invocation_id": target_id,
                        "_invocation_handle": target,
                        "authority": actor.as_value(),
                        "engine": "langgraph",
                        "_lineage_registry": registry,
                    },
                    _execution_fence=services.execution_ownership.native_fence(
                        target._execution_lease
                    ),
                )

        if downstream_effect and policy == "read_only":
            with pytest.raises(OwaError):
                await resume()
            with pytest.raises(OwaError):
                await resume()
        else:
            result = await resume()
            if not downstream_effect:
                assert result == {"value": 8}
            assert await resume() == result
        assert len(services.model.calls) == (
            3 if downstream_effect and policy == "reexecute" else 2 if downstream_effect else 1
        )
        async with saver_type.from_conn_string(database) as saver:
            retained = [item async for item in saver.alist(root)]
        assert [(item.checkpoint["id"], item.checkpoint) for item in retained] == [
            (item.checkpoint["id"], item.checkpoint) for item in checkpoints
        ]
    finally:
        if target is not None and target._execution_lease is not None:
            services.execution_ownership.release(target._execution_lease)
        services.execution_ownership.release(parent._execution_lease)
        await engine.shutdown()
        services.close()
