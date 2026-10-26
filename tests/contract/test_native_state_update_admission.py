"""A compiled update policy cannot substitute for an installed update implementation."""

from __future__ import annotations

import pytest
from engine_cases import engine_cases
from open_workflow_agent.errors import UnsupportedWorkflowFeature
from open_workflow_agent.native_state_policy import CAPABILITY
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_class", engine_cases())
@pytest.mark.parametrize("version", ["1.0.0", "99.0.0"])
async def test_required_state_update_policy_still_rejects_before_invocation_creation(
    services, engine_name, engine_class, version
):
    value = generate_default_workflow()
    value["do"] = [{"change": {"set": {"value": 1}}}]
    value["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [{"name": "langgraph.state.update", "version": version}],
            "nativeExtensionData": {
                CAPABILITY: {
                    "allowedTaskRefs": ["/do/0"],
                    "updateSchema": {"type": "object"},
                    "authorizationPolicyRef": "operators",
                }
            },
        }
    }
    if version != "1.0.0":
        value["document"]["metadata"]["owa"].pop("nativeExtensionData")
    engine = engine_class()
    await engine.initialize(services)
    try:
        before = services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocations"
        ).fetchone()[0]
        with pytest.raises(UnsupportedWorkflowFeature):
            await engine.compile(compile_workflow(value))
        after = services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocations"
        ).fetchone()[0]
        assert before == after and services.model.calls == []
    finally:
        await engine.shutdown()
