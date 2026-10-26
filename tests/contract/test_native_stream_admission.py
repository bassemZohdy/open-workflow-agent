"""Identical OWS declarations reject missing native installation on every engine."""

from __future__ import annotations

import pytest
from engine_cases import engine_cases
from open_workflow_agent.errors import UnsupportedWorkflowFeature
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_class", engine_cases())
@pytest.mark.parametrize("version", ["1.0.0", "99.0.0"])
async def test_required_native_stream_profile_rejects_before_invocation_creation(
    services, engine_name, engine_class, version
):
    definition = generate_default_workflow()
    definition["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {
                    "name": "langgraph.streams",
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
            await engine.compile(compile_workflow(definition))
        after = services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocations"
        ).fetchone()[0]
        assert before == after
        assert services.model.calls == []
        assert engine.capabilities().streaming is False
    finally:
        await engine.shutdown()
