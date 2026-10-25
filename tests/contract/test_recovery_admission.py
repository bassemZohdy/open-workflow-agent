"""Recovery must validate common/native state before accepting a resume."""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import OwaError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "damage",
    ["missing-native", "deleted-native", "missing-input", "changed-input", "native-unavailable"],
)
async def test_invalid_recovery_state_is_rejected_before_execution_or_state_change(
    tmp_path, engine_name, engine_type, backend, damage, monkeypatch
):
    if engine_name == "agent-framework":
        pytest.skip("optional common bridge has no qualified native checkpoint binding")
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )

    def services():
        return RuntimeServices(
            config,
            model=FakeModel({"must": "not-execute"}),
            database_root=None if datasource else tmp_path,
        )

    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "admission",
                "version": "1.0.0",
            },
            "do": [
                {"pause": {"wait": {"seconds": 300}}},
                {"effect": {"call": "agent:1.0.0@default"}},
            ],
        }
    )
    first = services()
    engine = engine_type()
    await engine.initialize(first)
    native_available = (
        engine.native.available if engine_name == "adk" else engine.native.task_nodes_available
    )
    if not native_available:
        await engine.shutdown()
        first.close()
        pytest.skip("independent native dependency environment is required")
    handle = first.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    original = {"original": "private-input-must-not-leak"}
    running = None
    try:
        if damage == "missing-native":
            first.invocations.bind_input(handle, original)
            first.invocations.update(handle, status="waiting")
        else:
            running = asyncio.create_task(engine.invoke(plan, handle, original))
            waiting = await asyncio.wait_for(asyncio.shield(running), timeout=10)
            assert waiting.status == "waiting", waiting.error
            assert handle.invocation_id not in engine._active
            assert handle.invocation_id not in engine._ownership_tasks
            assert handle._execution_lease is None
            assert first.invocations.read_input(handle) == original
        assert first.model.calls == []
    finally:
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        first.close()

    restarted = services()
    recovered = engine_type()
    await recovered.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None and persisted.status == "waiting"
    connection = restarted.invocations.connection
    try:
        if damage == "deleted-native":
            if engine_name == "adk":
                sessions = recovered.native._session_service(restarted.engine_database_path("adk"))
                try:
                    await sessions.delete_session(
                        app_name="open-workflow-agent",
                        user_id="owa-user",
                        session_id=handle.session_id,
                    )
                finally:
                    await recovered.native._close_sessions(sessions)
            else:
                from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

                async def delete_checkpoint(saver):
                    driver = LangGraphScopeExecutor(plan, saver, handle.invocation_id)
                    thread = driver.checkpoint_config("/do", 1)["configurable"]["thread_id"]
                    await saver.adelete_thread(thread)

                await recovered.native._with_saver(delete_checkpoint)
        elif damage == "missing-input":
            connection.execute(
                "DELETE FROM invocation_inputs WHERE invocation_id = ?", (handle.invocation_id,)
            )
            connection.commit()
        elif damage == "changed-input":
            connection.execute(
                "UPDATE invocation_inputs SET payload = ? WHERE invocation_id = ?",
                (json.dumps({"tampered": True}), handle.invocation_id),
            )
            connection.commit()
        elif damage == "native-unavailable":
            if engine_name == "adk":
                recovered.native.available = False
            else:
                # Remove only this instance's datasource-compatible native saver.
                recovered.native.database_path = "postgresql://unavailable.invalid/checkpoints"
                from open_workflow_agent_langgraph import native

                monkeypatch.setattr(native, "AsyncPostgresSaver", None)
        before = connection.execute(
            "SELECT payload FROM invocations WHERE invocation_id = ?", (handle.invocation_id,)
        ).fetchone()[0]
        with pytest.raises(OwaError) as failure:
            await asyncio.wait_for(recovered.resume(persisted, {"resume": True}, plan), 10)
        assert "private-input-must-not-leak" not in json.dumps(failure.value.as_dict())
        assert handle.engine_execution_reference not in json.dumps(failure.value.as_dict())
        assert restarted.model.calls == []
        assert persisted.status == "waiting"
        assert (
            connection.execute(
                "SELECT payload FROM invocations WHERE invocation_id = ?", (handle.invocation_id,)
            ).fetchone()[0]
            == before
        )
        if damage == "missing-input":
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM invocation_inputs WHERE invocation_id = ?",
                    (handle.invocation_id,),
                ).fetchone()[0]
                == 0
            )
    finally:
        await recovered.shutdown()
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_shared_session_keeps_each_invocations_original_input_after_restart(
    tmp_path, engine_name, engine_type, backend
):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    first = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    engine = engine_type()
    await engine.initialize(first)
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "shared-session",
                "version": "1.0.0",
            },
            "do": [
                {"pause": {"wait": {"seconds": 300}}},
                {"report": {"set": {"original": "${ $workflow.input }"}}},
            ],
        }
    )
    handles = []
    session_id = None
    try:
        for original in ({"generation": 1}, {"generation": 2}):
            handle = first.invocations.create(
                engine=engine_name,
                session_id=session_id,
                user_id="shared-user",
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            session_id = handle.session_id
            handles.append(handle)
            running = asyncio.create_task(engine.invoke(plan, handle, original))
            try:
                waiting = await asyncio.wait_for(asyncio.shield(running), 10)
                assert waiting.status == "waiting", waiting.error
                assert handle.invocation_id not in engine._active
                assert handle.invocation_id not in engine._ownership_tasks
                assert handle._execution_lease is None
                assert first.invocations.read_input(handle) == original
            finally:
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
        assert len({handle.invocation_id for handle in handles}) == 2
        assert {handle.session_id for handle in handles} == {session_id}
        assert first.model.calls == []
    finally:
        await engine.shutdown()
        first.close()
    restarted = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    recovered = engine_type()
    await recovered.initialize(restarted)
    try:
        for index, handle in enumerate(handles, 1):
            persisted = restarted.invocations.get(handle.invocation_id)
            result = await asyncio.wait_for(recovered.resume(persisted, None, plan), 10)
            assert result.status == "completed", result.error
            assert result.output == {"original": {"generation": index}}
            assert restarted.invocations.read_input(persisted) == {"generation": index}
        assert restarted.model.calls == []
    finally:
        await recovered.shutdown()
        restarted.close()
