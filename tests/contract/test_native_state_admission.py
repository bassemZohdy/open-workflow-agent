"""The same public metadata rejects an unavailable native state profile."""

from __future__ import annotations

import pytest
from engine_cases import engine_cases
from open_workflow_agent.errors import UnsupportedWorkflowFeature
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_class", engine_cases())
@pytest.mark.parametrize("version", ["1.0.0", "99.0.0"])
async def test_required_native_state_read_rejects_before_creating_invocations(
    services, engine_name, engine_class, version
):
    source = generate_default_workflow()
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {
                    "name": "langgraph.state.read",
                    "version": version,
                    "required": True,
                }
            ]
        }
    }
    engine = engine_class()
    await engine.initialize(services)
    try:
        before = services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocations"
        ).fetchone()[0]
        with pytest.raises(UnsupportedWorkflowFeature):
            await engine.compile(compile_workflow(source))
        after = services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocations"
        ).fetchone()[0]
        assert before == after and services.model.calls == []
    finally:
        await engine.shutdown()
