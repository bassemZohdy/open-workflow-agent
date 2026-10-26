"""Private preflight inspects actual OWA native state without enabling mutation."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from native_stream_test_support import authority, configuration
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.errors import OwaError, UnsupportedWorkflowFeature
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.native_state_policy import CAPABILITY
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.storage import open_storage
from open_workflow_agent.timers import TimerSuspension
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow

pytest.importorskip("langgraph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine, native
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor
from open_workflow_agent_langgraph.native_state import state_reference
from open_workflow_agent_langgraph.native_state_preflight import prepare_native_state_update


@pytest.fixture(params=["sqlite", "postgresql"])
async def preflight(request, tmp_path):
    backend = request.param[0] if isinstance(request.param, tuple) else request.param
    channels = isinstance(request.param, tuple) and request.param[1] == "channels"
    config = configuration()
    config.observability.native_streams = None
    config.execution_authorization.policy.rules[0].actions.append("state.update")
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
        config.persistence.datasource = datasource
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if backend == "postgresql" else tmp_path
    )
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    source = generate_default_workflow()
    source["do"] = [
        {"first": {"set": {"value": 1}}},
        {"nested": {"do": [{"inner": {"set": {"nested": True, "value": "${ .value }"}}}]}},
        {"pause": {"wait": {"seconds": 300}}},
        {"last": {"set": {"value": "${ .value + 1 }"}}},
    ]
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [{"name": "langgraph.state.update", "version": "1.0.0"}],
            "nativeExtensionData": {
                CAPABILITY: {
                    "allowedTaskRefs": ["/do/0", "/do/3"],
                    "updateSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                    "authorizationPolicyRef": config.execution_authorization.id,
                }
            },
        }
    }
    if channels:
        owa = source["document"]["metadata"]["owa"]
        owa["nativeExtensions"].append({"name": "langgraph.channels", "version": "1.0.0"})
        owa["nativeExtensionData"]["langgraph.channels@1.0.0"] = {
            "channels": [
                {
                    "id": "items",
                    "reducer": "last_value",
                    "initial": None,
                    "writes": [{"task": "/do/0"}],
                }
            ],
            "reads": [],
        }
    plan = compile_workflow(source)
    actor = authority(services, "alice")
    handle = services.invocations.create(
        engine="langgraph",
        session_id=None,
        user_id=None,
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    lease = services.execution_ownership.acquire(handle, seconds=300)
    handle._execution_lease = lease
    try:
        # Ordinary engine admission must remain closed. This private fixture
        # runs only unchanged OWA set/wait tasks through the adapter directly;
        # it does not qualify public update admission or workflow mutation.
        with pytest.raises(UnsupportedWorkflowFeature):
            await engine.compile(plan)
        services.execution_authorizations.reserve_invocation(handle, actor, input_data={})
        with authority_scope(actor):
            async with services.recovery_definitions.owned_profile("langgraph"):
                services.recovery_definitions.admit(plan, handle, {}, execution_lease=lease)
            with pytest.raises(TimerSuspension):
                await engine.native.execute_plan(
                    engine.executor,
                    plan,
                    {},
                    metadata={
                        "invocation_id": handle.invocation_id,
                        "_invocation_handle": handle,
                        "authority": actor.as_value(),
                        "engine": "langgraph",
                    },
                    invocation_id=handle.invocation_id,
                    _execution_fence=services.execution_ownership.native_fence(lease),
                )
        services.invocations.update(handle, status="waiting")
        saver_type = (
            native.AsyncPostgresSaver if backend == "postgresql" else native.AsyncSqliteSaver
        )
        root = LangGraphScopeExecutor(plan, None, handle.invocation_id).checkpoint_config("/do", 1)
        path = services.engine_database_path("langgraph", create_namespace=False)
        async with saver_type.from_conn_string(path) as saver:
            checkpoint = await saver.aget_tuple(root)
        reference = state_reference(
            handle.invocation_id,
            plan.fingerprint,
            checkpoint.config["configurable"]["checkpoint_id"],
        )
        yield services, engine, handle, actor, reference, checkpoint, saver_type, plan
    finally:
        if handle._execution_lease is not None:
            services.execution_ownership.release(handle._execution_lease)
        await engine.shutdown()
        services.close()


async def prepare(preflight, **changes):
    services, _engine, handle, actor, reference, *_ = preflight
    options = {
        "request_id": uuid4().hex * 2,
        "base_reference": reference,
        "task_reference": "/do/0/first",
        "values": {"value": 7},
    }
    return await prepare_native_state_update(services, handle, actor, **(options | changes))


def counts(preflight):
    services, _, handle, *_ = preflight
    connection = services.effect_journal.connection
    tables = ("workflow_effects", "workflow_effect_invocations", "invocations")
    return {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables
    }


@pytest.mark.asyncio
async def test_actual_owa_preflight_preserves_state_and_has_no_common_intent_or_model_call(
    preflight,
):
    services, engine, handle, _actor, reference, checkpoint, _, plan = preflight
    before = counts(preflight)
    proposal = await prepare(preflight)
    assert proposal.request.base_reference == reference
    assert proposal.request.values == {"value": 7}
    assert proposal.request.policy_digest == plan.native_state_updates.digest
    proposal.verify_base(checkpoint.checkpoint["channel_values"])
    changed = json.loads(proposal.base_native_json)
    changed["next_node"] = "__end__"
    with pytest.raises(OwaError):
        proposal.verify_base(changed)
    assert counts(preflight) == before and services.model.calls == []
    assert CAPABILITY not in engine.capabilities().native_extensions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"values": {"value": "invalid"}},
        {"base_reference": "0" * 64},
        {"task_reference": "/do/3/last"},
        {"task_reference": "/do/2/pause"},
        {"bounds": NativeStateConfig(max_history=1, max_page_size=1)},
    ],
)
async def test_schema_stale_base_unexecuted_attribution_and_scope_budget_fail_closed(
    preflight, changes
):
    before = counts(preflight)
    with pytest.raises(OwaError):
        await prepare(preflight, **changes)
    assert counts(preflight) == before and preflight[0].model.calls == []


@pytest.mark.asyncio
async def test_other_principal_and_changed_common_identity_are_denied_before_native_reads(
    preflight, monkeypatch
):
    services, _engine, handle, actor, reference, _checkpoint, saver_type, _plan = preflight
    reads = []

    async def forbidden(*_args, **_kwargs):
        reads.append(True)
        raise AssertionError("native reads must follow owner validation")

    monkeypatch.setattr(saver_type, "aget_tuple", forbidden)
    with pytest.raises(OwaError):
        await prepare_native_state_update(
            services,
            handle,
            authority(services, "bob"),
            request_id="a" * 64,
            base_reference=reference,
            task_reference="/do/0/first",
            values={"value": 7},
        )
    assert reads == []
    changed = replace(handle, user_id="changed-correlation")
    changed._execution_lease = handle._execution_lease
    with pytest.raises(OwaError):
        await prepare_native_state_update(
            services,
            changed,
            actor,
            request_id="b" * 64,
            base_reference=reference,
            task_reference="/do/0/first",
            values={"value": 7},
        )
    assert reads == []


@pytest.mark.asyncio
async def test_corrupt_native_state_is_not_adopted_by_preflight(preflight):
    services, _engine, handle, _actor, _reference, _checkpoint, _saver, plan = preflight
    path = services.engine_database_path("langgraph", create_namespace=False)
    connection = open_storage(path, "owa_langgraph")
    thread = LangGraphScopeExecutor(plan, None, handle.invocation_id).checkpoint_config("/do", 1)[
        "configurable"
    ]["thread_id"]
    try:
        if connection.is_postgresql:
            connection.execute(
                "UPDATE checkpoints SET checkpoint=jsonb_set(checkpoint,"
                "'{channel_values,next_node}', '\"__end__\"'::jsonb) WHERE thread_id=?",
                (thread,),
            )
        else:
            connection.execute(
                "UPDATE checkpoints SET checkpoint=? WHERE thread_id=?", (b"corrupt", thread)
            )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(OwaError):
        await prepare(preflight)
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_native_byte_limit_is_checked_before_sdk_decoding(preflight, monkeypatch):
    saver_type = preflight[6]
    reads = []

    async def forbidden(*_args, **_kwargs):
        reads.append(True)
        raise AssertionError("oversized state must not reach SDK decoding")

    monkeypatch.setattr(saver_type, "aget_tuple", forbidden)
    with pytest.raises(OwaError):
        await prepare(preflight, bounds=NativeStateConfig(max_native_bytes=1024))
    assert reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
async def test_completed_child_scope_must_remain_present_and_consistent(preflight, damage):
    services, _engine, handle, _actor, _reference, _checkpoint, _saver, plan = preflight
    driver = LangGraphScopeExecutor(plan, None, handle.invocation_id)
    child = next(scope.reference for scope in plan.scopes if scope.reference != "/do")
    thread = driver.checkpoint_config(child, 1)["configurable"]["thread_id"]
    connection = open_storage(
        services.engine_database_path("langgraph", create_namespace=False), "owa_langgraph"
    )
    try:
        if damage == "missing":
            connection.execute("DELETE FROM checkpoints WHERE thread_id=?", (thread,))
        elif connection.is_postgresql:
            connection.execute(
                "UPDATE checkpoints SET checkpoint=jsonb_set(checkpoint,"
                "'{channel_values,next_node}', '\"invalid-node\"'::jsonb) WHERE thread_id=?",
                (thread,),
            )
        else:
            connection.execute(
                "UPDATE checkpoints SET checkpoint=? WHERE thread_id=?", (b"corrupt", thread)
            )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(OwaError):
        await prepare(preflight)
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_native_read_timeout_cleans_up_before_a_later_preflight(preflight, monkeypatch):
    saver_type = preflight[6]
    original = saver_type.aget_tuple
    before = counts(preflight)

    async def paused(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(saver_type, "aget_tuple", paused)
    with pytest.raises(OwaError):
        await prepare(preflight, bounds=NativeStateConfig(timeout_seconds=0.02))
    monkeypatch.setattr(saver_type, "aget_tuple", original)
    assert (await prepare(preflight)).request.values == {"value": 7}
    assert counts(preflight) == before
