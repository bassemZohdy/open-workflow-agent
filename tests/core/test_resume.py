from __future__ import annotations

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


@pytest.mark.asyncio
async def test_reference_resume_survives_service_restart(tmp_path):
    config = RuntimeConfig()
    first_services = RuntimeServices(
        config, model=FakeModel({"response": "first"}), database_root=tmp_path
    )
    first_engine = PortableWorkflowEngine()
    await first_engine.initialize(first_services)
    plan = compile_workflow()
    handle = first_services.invocations.create(
        engine="reference",
        session_id="session-1",
        user_id="user-1",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    first = await first_engine.invoke(plan, handle, {"question": "before restart"})
    assert first.status == "completed"
    first_services.close()

    second_services = RuntimeServices(
        config, model=FakeModel({"response": "resumed"}), database_root=tmp_path
    )
    second_engine = PortableWorkflowEngine()
    await second_engine.initialize(second_services)
    persisted = second_services.invocations.get(handle.invocation_id)
    assert persisted is not None
    resumed = await second_engine.resume(persisted, {"question": "after restart"}, plan)
    assert resumed.status == "completed"
    assert resumed.session_id == "session-1"
    second_services.close()
