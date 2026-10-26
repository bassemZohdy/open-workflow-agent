"""Declared lineage policy never substitutes for an installed fork implementation."""

import pytest
from engine_cases import engine_cases
from open_workflow_agent.errors import UnsupportedWorkflowFeature
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_class", engine_cases())
@pytest.mark.parametrize("kind", ["replay", "branch"])
@pytest.mark.parametrize("version", ["1.0.0", "99.0.0"])
async def test_required_native_lineage_profile_rejects_before_invocation_creation(
    services, engine_name, engine_class, kind, version
):
    source = generate_default_workflow()
    source["do"] = [{"first": {"set": {"value": 1}}}]
    owa = {"nativeExtensions": [{"name": "langgraph.state." + kind, "version": version}]}
    if version == "1.0.0":
        field = "replayEffectPolicy" if kind == "replay" else "branchEffectPolicy"
        owa["nativeExtensionData"] = {
            "langgraph.state." + kind + "@1.0.0": {
                "authorizationPolicyRef": "operators",
                field: "read_only",
            }
        }
        if kind == "branch":
            owa["nativeExtensions"].append({"name": "langgraph.state.update", "version": "1.0.0"})
            owa["nativeExtensionData"]["langgraph.state.update@1.0.0"] = {
                "allowedTaskRefs": ["/do/0"],
                "updateSchema": {"type": "object"},
                "authorizationPolicyRef": "operators",
            }
    source["document"]["metadata"] = {"owa": owa}
    engine = engine_class()
    await engine.initialize(services)
    try:
        before = services.invocations.connection.execute(
            "SELECT COUNT(*) FROM invocations"
        ).fetchone()[0]
        with pytest.raises(UnsupportedWorkflowFeature):
            await engine.compile(compile_workflow(source))
        assert (
            services.invocations.connection.execute("SELECT COUNT(*) FROM invocations").fetchone()[
                0
            ]
            == before
        )
        assert services.model.calls == []
    finally:
        await engine.shutdown()
