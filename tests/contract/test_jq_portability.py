from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig, SandboxConfig
from open_workflow_agent.sandbox import compile_sandbox_workflow
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


def _handle(services, plan, engine_name):
    return services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_pinned_jq_types_and_modes_are_isolated_between_invocations(
    services, engine_name, engine_type
):
    engine = engine_type()
    await engine.initialize(services)
    data = {"items": [3, 1, 3], "literal": "${ env }"}
    original = copy.deepcopy(data)
    try:

        async def invoke(mode):
            workflow = generate_default_workflow()
            workflow["evaluate"] = {"language": "jq", "mode": mode}
            workflow["do"] = [
                {
                    "shape": {
                        "set": {
                            "sorted": "${ .items | map(. * 2) | sort | unique }",
                            "sum": "${ reduce .items[] as $x (0; . + $x) }",
                            "typed": '${ [true, null, 2.5, {name: "Ada"}] }',
                            "literal": "${ .literal }",
                            "mode": ".items | length",
                            "text": "v=${ [true,null] }",
                        }
                    }
                }
            ]
            plan = compile_workflow(workflow)
            result = await engine.invoke(
                await engine.compile(plan), _handle(services, plan, engine_name), data
            )
            assert result.status == "completed", result.error
            assert result.output == {
                "sorted": [2, 6],
                "sum": 7,
                "typed": [True, None, 2.5, {"name": "Ada"}],
                "literal": "${ env }",
                "text": "v=[true,null]",
                "mode": ".items | length" if mode == "strict" else 3,
            }

        await asyncio.gather(invoke("strict"), invoke("loose"))
        assert data == original
    finally:
        await engine.shutdown()


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize(
    "source", ["empty", "1,2", 'error("secret-value")', "env", "nan", "infinite"]
)
@pytest.mark.asyncio
async def test_expression_failures_have_identical_common_errors(
    services, engine_name, engine_type, source
):
    workflow = generate_default_workflow()
    workflow["do"] = [{"fail": {"set": {"value": "${ " + source + " }"}}}]
    plan = compile_workflow(workflow)
    engine = engine_type()
    await engine.initialize(services)
    try:
        result = await engine.invoke(
            await engine.compile(plan), _handle(services, plan, engine_name), {}
        )
        assert result.status == "faulted"
        assert result.error and result.error["code"] == "expression_error"
        assert "secret-value" not in json.dumps(result.error)
    finally:
        await engine.shutdown()


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_jq_sandbox_inputs_use_json_and_script_code_stays_literal(
    tmp_path: Path, engine_name, engine_type
):
    config = RuntimeConfig(
        sandbox=SandboxConfig(
            enabled=True,
            memory_bytes=None,
            file_size_bytes=None if os.name == "nt" else 67_108_864,
            workspace_root=str(tmp_path / "sandbox"),
        )
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    engine = engine_type()
    await engine.initialize(services)
    workflow = generate_default_workflow()
    workflow["do"] = [
        {
            "execute": {
                "run": {
                    "script": {
                        "language": "python",
                        "code": (
                            "import json,sys,os\n"
                            'print(json.dumps({"argv":sys.argv[1:],'
                            '"stdin":sys.stdin.read(),"env":os.environ["VALUE"],'
                            '"literal":"${ invalid"}))'
                        ),
                        "arguments": ["${ true }", "${ .value }"],
                        "stdin": "${ [true,null] }",
                        "environment": {"VALUE": "${ {value: .value} }"},
                    }
                }
            }
        }
    ]
    try:
        plan = compile_sandbox_workflow(workflow, sandbox=config.sandbox)
        result = await engine.invoke(
            await engine.compile(plan), _handle(services, plan, engine_name), {"value": 2}
        )
        assert result.status == "completed", result.error
        assert json.loads(result.output["stdout"]) == {
            "argv": ["true", "2"],
            "stdin": "[true,null]",
            "env": '{"value":2}',
            "literal": "${ invalid",
        }
    finally:
        await engine.shutdown()
        services.close()
