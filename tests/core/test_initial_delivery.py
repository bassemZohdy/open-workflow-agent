"""Common queue admission/fencing controls; synthetic profiles are not SDK evidence."""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import InvocationStateConflict, OwaError, WorkflowExecutionError
from open_workflow_agent.initial_delivery import delivery_identity
from open_workflow_agent.recovery_definitions import PROFILE, RecoveryDefinitionArchive
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.timing import root_clock_identity
from open_workflow_agent.workflow import compile_workflow


@pytest.fixture(params=["sqlite", "postgresql"])
def delivery_tree(tmp_path, request, monkeypatch):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if request.param == "postgresql" else None
    if request.param == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    model = FakeModel({"answer": "original"})
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=model,
        database_root=None if datasource else tmp_path,
    )
    services.recovery_definitions = RecoveryDefinitionArchive(
        services.effect_journal,
        services.invocations,
        profile_provider=lambda engine: {
            "profile": PROFILE,
            "engine": engine,
            "backend": request.param,
            "artifact": "synthetic-common-queue-test",
            "continuation_supported": True,
        },
    )

    async def no_timer_workers(_dispatcher):
        pass

    monkeypatch.setattr(TimerDispatcher, "start", no_timer_workers)
    created = []
    create = services.invocations.create

    def tracked_create(**kwargs):
        handle = create(**kwargs)
        created.append(handle.invocation_id)
        return handle

    monkeypatch.setattr(services.invocations, "create", tracked_create)
    try:
        yield services, model
    finally:
        try:
            # Negative cases deliberately retain damaged/ready deliveries for
            # their snapshots. Remove only this fixture's queue entries after
            # its engine has drained; preserve original invocation/proof rows.
            connection = services.initial_deliveries.connection
            for invocation_id in created:
                connection.execute(
                    "DELETE FROM initial_invocation_deliveries WHERE invocation_id=?",
                    (invocation_id,),
                )
            connection.commit()
        finally:
            services.close()


def new_handle(services, plan):
    return services.invocations.create(
        engine="base",
        session_id="context",
        user_id="correlation",
        owner_principal="owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


@pytest.mark.asyncio
async def test_ack_boundary_commits_archive_input_identity_and_queue_without_business(
    delivery_tree,
):
    services, model = delivery_tree
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    try:
        plan = compile_workflow()
        handle = new_handle(services, plan)
        await engine.prepare_initial_delivery(
            plan, handle, {"question": "original"}, skill_id="skill"
        )
        binding = services.initial_deliveries.get(handle)
        assert binding is not None and binding.status == "ready" and binding.skill_id == "skill"
        assert services.resolve_original_definition(handle).fingerprint == plan.fingerprint
        assert services.invocations.read_input(handle) == {"question": "original"}
        assert model.calls == []
        assert services.effect_journal.get(delivery_identity(handle)).status == "completed"
        result = await engine.dispatch_initial_delivery(handle, lambda handle, plan: None)
        assert result.status == "completed" and result.output == {"answer": "original"}
        assert len(model.calls) == 1 and services.initial_deliveries.get(handle).status == "settled"
        again = await engine.dispatch_initial_delivery(handle, lambda handle, plan: None)
        assert again == result and len(model.calls) == 1

        # A stop between the terminal commit and queue cleanup must preserve
        # the first result and allow cleanup without repeating model work.
        stopped_handle = new_handle(services, plan)
        await engine.prepare_initial_delivery(plan, stopped_handle, {}, skill_id=None)

        class DeliveryStopped(BaseException):
            pass

        def stop_before_cleanup(current):
            assert current.status == "completed"
            raise DeliveryStopped

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(services.initial_deliveries, "settle", stop_before_cleanup)
            with pytest.raises(DeliveryStopped):
                await engine.dispatch_initial_delivery(stopped_handle, lambda handle, plan: None)
        terminal = services.invocations.get(stopped_handle.invocation_id)
        assert terminal.status == "completed"
        assert services.initial_deliveries.get(terminal).status == "ready"
        assert len(model.calls) == 2
        recovered = await engine.dispatch_initial_delivery(terminal, lambda handle, plan: None)
        assert recovered.status == "completed" and recovered.output == {"answer": "original"}
        assert services.initial_deliveries.get(terminal).status == "settled"
        assert len(model.calls) == 2
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["queue", "receipt", "index", "payload", "input", "owner", "archive"]
)
async def test_invalid_delivery_rejects_before_new_lease_or_business(delivery_tree, damage):
    services, model = delivery_tree
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    try:
        plan = compile_workflow()
        handle = new_handle(services, plan)
        await engine.prepare_initial_delivery(plan, handle, {"question": "original"}, skill_id=None)
        connection = services.effect_journal.connection
        identity = delivery_identity(handle)
        if damage == "queue":
            connection.execute(
                "DELETE FROM initial_invocation_deliveries WHERE invocation_id=?",
                (handle.invocation_id,),
            )
        elif damage in {"receipt", "index"}:
            table = "workflow_effects" if damage == "receipt" else "workflow_effect_invocations"
            connection.execute(f"DELETE FROM {table} WHERE identity=?", (identity.key,))
        elif damage == "payload":
            connection.execute(
                "UPDATE initial_invocation_deliveries SET payload=? WHERE invocation_id=?",
                ('{"profile":"changed"}', handle.invocation_id),
            )
        elif damage == "input":
            connection.execute(
                "UPDATE invocation_inputs SET payload=? WHERE invocation_id=?",
                ('{"question":"changed"}', handle.invocation_id),
            )
        elif damage == "owner":
            saved = json.loads(handle._persisted_payload)
            saved["owner_principal"] = "foreign"
            connection.execute(
                "UPDATE invocations SET payload=? WHERE invocation_id=?",
                (json.dumps(saved), handle.invocation_id),
            )
            handle = services.invocations.get(handle.invocation_id)
        else:
            connection.execute(
                "DELETE FROM recovery_invocation_definitions WHERE invocation_id=?",
                (handle.invocation_id,),
            )
        connection.commit()
        before = connection.execute(
            "SELECT * FROM invocation_ownership WHERE invocation_id=?", (handle.invocation_id,)
        ).fetchall()
        with pytest.raises(OwaError):
            await engine.dispatch_initial_delivery(handle, lambda handle, plan: None)
        after = connection.execute(
            "SELECT * FROM invocation_ownership WHERE invocation_id=?", (handle.invocation_id,)
        ).fetchall()
        assert before == after and model.calls == []
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_ownership_and_capacity_reject_ack_without_receiver_dispatch(delivery_tree):
    services, model = delivery_tree
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    try:
        plan = compile_workflow()
        handle = new_handle(services, plan)
        with pytest.raises(InvocationStateConflict):
            services.initial_deliveries.admit(handle, skill_id=None)
        services.initial_deliveries.max_pending = 1
        await engine.prepare_initial_delivery(plan, handle, {}, skill_id=None)
        other = new_handle(services, plan)
        with pytest.raises(WorkflowExecutionError, match="capacity"):
            await engine.prepare_initial_delivery(plan, other, {}, skill_id=None)
        assert services.initial_deliveries.get(other) is None and model.calls == []
        assert services.invocations.read_input(other) == {}
        # A stop between archive and ready admission has no acknowledged delivery.
        assert services.resolve_original_definition(other).fingerprint == plan.fingerprint
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_fair_dispatch_skips_bad_binding_and_rechecks_current_authority(delivery_tree):
    services, model = delivery_tree
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    try:
        plan = compile_workflow()
        handles = [new_handle(services, plan) for _ in range(3)]
        for handle in handles:
            await engine.prepare_initial_delivery(plan, handle, {}, skill_id="skill")
        handles.sort(key=lambda item: item.invocation_id)
        connection = services.effect_journal.connection
        connection.execute(
            "UPDATE initial_invocation_deliveries SET digest='bad' WHERE invocation_id=?",
            (handles[0].invocation_id,),
        )
        connection.commit()
        dispatcher = engine._initial_delivery_dispatcher
        dispatcher._authorize = lambda handle, skill, plan: (
            handle.invocation_id != handles[1].invocation_id
        )
        await dispatcher.tick()
        await asyncio.gather(*dispatcher._workers.values())
        assert services.invocations.get(handles[2].invocation_id).status == "completed"
        assert len(model.calls) == 1 and len(dispatcher._errors) == 2
        assert services.invocations.get(handles[1].invocation_id).status == "running"
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["expired", "missing", "changed"])
async def test_ack_retains_original_deadline_and_rejects_clock_damage(delivery_tree, condition):
    services, model = delivery_tree
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    now = [1000.0]
    engine.executor._absolute_clock = lambda: now[0]
    try:
        source = json.loads(compile_workflow().source_json)
        source["timeout"] = {"after": {"seconds": 10}}
        plan = compile_workflow(source)
        handle = new_handle(services, plan)
        await engine.prepare_initial_delivery(plan, handle, {}, skill_id=None)
        identity = root_clock_identity(handle.invocation_id, plan.fingerprint)
        original = services.effect_journal.get(identity)
        assert original.output["started_at"] == 1000.0 and model.calls == []
        now[0] = 1020.0
        connection = services.effect_journal.connection
        if condition == "expired":
            result = await engine.dispatch_initial_delivery(handle, lambda handle, plan: None)
            assert result.status == "faulted" and result.error["details"]["status"] == 408
            assert services.effect_journal.get(identity) == original
        else:
            if condition == "missing":
                connection.execute("DELETE FROM workflow_effects WHERE identity=?", (identity.key,))
            else:
                row = connection.execute(
                    "SELECT payload FROM workflow_effects WHERE identity=?", (identity.key,)
                ).fetchone()
                value = json.loads(row[0])
                value["output"]["started_at"] = 1020.0
                connection.execute(
                    "UPDATE workflow_effects SET payload=? WHERE identity=?",
                    (json.dumps(value), identity.key),
                )
            connection.commit()
            before = connection.execute(
                "SELECT * FROM invocation_ownership WHERE invocation_id=?", (handle.invocation_id,)
            ).fetchall()
            with pytest.raises(WorkflowExecutionError):
                await engine.dispatch_initial_delivery(handle, lambda handle, plan: None)
            assert (
                connection.execute(
                    "SELECT * FROM invocation_ownership WHERE invocation_id=?",
                    (handle.invocation_id,),
                ).fetchall()
                == before
            )
        assert model.calls == []
    finally:
        await engine.shutdown()
