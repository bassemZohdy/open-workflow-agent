"""Invalid original-definition evidence must not reserve timer delivery."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import compile_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("damage", ["missing-binding", "missing-archive", "profile-change"])
async def test_original_definition_preflight_precedes_mutable_timer_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, damage: str
) -> None:
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")

    async def explicit_delivery(_dispatcher: TimerDispatcher) -> None:
        return None

    monkeypatch.setattr(TimerDispatcher, "start", explicit_delivery)
    services = RuntimeServices(
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}}),
        model=FakeModel(),
        database_root=None if datasource else tmp_path,
    )
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "archive-timer",
                "version": "1.0.0",
            },
            "do": [{"pause": {"wait": {"seconds": 60}}}, {"done": {"set": {"done": True}}}],
        }
    )
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    engine.executor._absolute_clock = lambda: 1000.0
    handle = services.invocations.create(
        engine="base",
        session_id=None,
        user_id="correlation",
        owner_principal="owner",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        first = await engine.invoke(plan, handle, {"original": True})
        assert first.status == "waiting"
        archive = services.recovery_definitions
        if damage == "missing-binding":
            archive.connection.execute(
                "DELETE FROM recovery_invocation_definitions WHERE invocation_id=?",
                (handle.invocation_id,),
            )
            archive.connection.commit()
        elif damage == "missing-archive":
            archive.connection.execute(
                "DELETE FROM recovery_definitions WHERE fingerprint=?",
                (plan.fingerprint,),
            )
            archive.connection.commit()
        else:
            original_profile = archive.profile_provider
            archive.profile_provider = lambda name: {
                **original_profile(name),
                "deployment_revision": "changed",
            }
        before = services.timers.connection.execute(
            "SELECT * FROM workflow_timers WHERE invocation_id=? ORDER BY identity",
            (handle.invocation_id,),
        ).fetchall()
        assert len(before) == 1 and before[0][7] is None
        ownership = services.execution_ownership.connection.execute(
            "SELECT * FROM invocation_ownership WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchall()
        saved = services.invocations.get(handle.invocation_id)
        assert saved is not None
        payload = saved._persisted_payload
        dispatcher = engine._timer_dispatcher
        assert dispatcher is not None
        assert await dispatcher.tick(now=1060.0, invocation_id=handle.invocation_id) == 0
        assert not dispatcher._workers
        assert len(dispatcher._errors) == 1
        assert isinstance(dispatcher._errors[0], InvocationStateError)
        assert (
            services.timers.connection.execute(
                "SELECT * FROM workflow_timers WHERE invocation_id=? ORDER BY identity",
                (handle.invocation_id,),
            ).fetchall()
            == before
        )
        assert (
            services.execution_ownership.connection.execute(
                "SELECT * FROM invocation_ownership WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchall()
            == ownership
        )
        after = services.invocations.get(handle.invocation_id)
        assert after is not None and after._persisted_payload == payload
        assert isinstance(services.model, FakeModel) and services.model.calls == []
    finally:
        await engine.shutdown()
        services.close()
