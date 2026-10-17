"""Transformed executable limits reject before independent job acceptance."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig, SandboxConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import SandboxPolicyError
from open_workflow_agent.sandbox import compile_sandbox_workflow
from open_workflow_agent.sandbox.backends.internal import InternalSandboxBackend
from open_workflow_agent.sandbox.contract import SandboxExecutionRequest, SandboxSecretReference
from open_workflow_agent.sandbox.manager import SandboxManager
from open_workflow_agent.services import RuntimeServices


@pytest.mark.parametrize(
    "case", ["stdin", "arguments", "runtime", "container", "shell", "literal", "secret"]
)
def test_internal_request_preflight_creates_no_workspace_or_process(tmp_path, case):
    config = SandboxConfig(
        enabled=True,
        allow_shell=True,
        max_input_bytes=64,
        workspace_root=str(tmp_path / "workspace"),
        executable_search_path=str(Path(sys.executable).parent),
    )
    request = {
        "execution_id": "preflight",
        "kind": "script",
        "script_language": "python",
        "script_code": "print('ok')",
    }
    request.update(
        {
            "stdin": {"stdin": "x" * 65},
            "arguments": {"arguments": ("x" * 65,)},
            "runtime": {"script_language": "javascript"},
            "container": {"kind": "container", "image": "forbidden"},
            "shell": {"kind": "shell", "command": "owa-not-installed-preflight-witness"},
            "literal": {"environment": (("EXTRA", "literal-not-allowed"),)},
            "secret": {"environment": (("EXTRA", SandboxSecretReference("not-approved")),)},
        }[case]
    )
    backend = InternalSandboxBackend(config)
    with pytest.raises(SandboxPolicyError):
        SandboxManager(backend).validate_job_request(SandboxExecutionRequest(**request))
    assert backend._active == {} and not Path(config.workspace_root).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["stdin", "arguments"])
async def test_oversized_evaluated_request_has_no_job_or_completed_acceptance(tmp_path, field):
    config = RuntimeConfig.model_validate(
        {
            "sandbox": {
                "enabled": True,
                "background": {"enabled": True},
                "max_input_bytes": 64,
                "workspace_root": str(tmp_path / "workspace"),
                "file_size_bytes": None,
                "process_count": None,
                "inherited_environment": ["SYSTEMROOT"] if os.name == "nt" else [],
            }
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    engine = PortableWorkflowEngine()
    script = {
        "language": "python",
        "code": "print('ok')",
        field: "${ .payload }" if field == "stdin" else ["${ .payload }"],
    }
    plan = compile_sandbox_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "tests",
                "name": "background-input",
                "version": "1.0.0",
            },
            "do": [{"job": {"run": {"await": False, "script": script}}}],
        },
        sandbox=config.sandbox,
    )
    try:
        await engine.initialize(services)
        handle = services.invocations.create(
            engine="portable",
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        result = await engine.invoke(plan, handle, {"payload": "x" * 65})
        assert result.status == "faulted" and result.error["code"] == "sandbox_policy_error"
        assert (
            services.effect_journal.connection.execute(
                "SELECT COUNT(*) FROM sandbox_jobs"
            ).fetchone()[0]
            == 0
        )
        assert services.model.calls == []
        operations = [
            json.loads(row[0])["operation"]
            for row in services.effect_journal.connection.execute(
                "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=?",
                (handle.invocation_id,),
            ).fetchall()
        ]
        assert "run:script" not in operations
        assert not any(operation.startswith("policy:sandbox-job") for operation in operations)
        assert not (Path(config.sandbox.workspace_root) / ".owa-job-supervisors").exists()
    finally:
        await engine.shutdown()
        services.close()
