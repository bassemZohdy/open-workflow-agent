from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import (
    SandboxOutputLimitError,
    SandboxProcessError,
    SandboxResourceLimitError,
    SandboxTimeoutError,
    UnsupportedWorkflowFeature,
)
from open_workflow_agent.sandbox import (
    InternalSandboxBackend,
    SandboxExecutionRequest,
    SandboxSecretReference,
    compile_sandbox_workflow,
    validate_sandbox_capabilities,
)


def _workflow(run: dict[str, object]) -> dict[str, object]:
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "sandbox-test",
            "name": "sandbox-test",
            "version": "1.0.0",
        },
        "do": [{"execute": {"run": run}}],
    }


def test_sandbox_policy_is_strict_and_disabled_by_default() -> None:
    with pytest.raises(ValidationError):
        SandboxConfig(script_runtimes=["node"])
    with pytest.raises(UnsupportedWorkflowFeature):
        compile_sandbox_workflow(
            _workflow({"script": {"language": "python", "code": "print('x')"}}),
            sandbox=SandboxConfig(),
        )


def test_container_and_external_script_stay_fail_closed() -> None:
    config = SandboxConfig(enabled=True)
    with pytest.raises(UnsupportedWorkflowFeature):
        validate_sandbox_capabilities(
            _workflow({"container": {"image": "example.invalid/image:latest"}}),
            sandbox=config,
        )
    with pytest.raises(UnsupportedWorkflowFeature):
        validate_sandbox_capabilities(
            _workflow(
                {
                    "script": {
                        "language": "python",
                        "source": {"endpoint": {"uri": "https://example.invalid/script.py"}},
                    }
                }
            ),
            sandbox=config,
        )


@pytest.mark.asyncio
async def test_internal_script_uses_private_workspace_and_filtered_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OWA_SANDBOX_SHOULD_NOT_LEAK", "secret")
    root = tmp_path / "sandbox"
    backend = InternalSandboxBackend(
        SandboxConfig(enabled=True, workspace_root=str(root), memory_bytes=None)
    )
    result = await backend.execute(
        SandboxExecutionRequest(
            execution_id="script-success",
            kind="script",
            script_language="python",
            script_code=(
                "import os\n"
                "from pathlib import Path\n"
                "Path('created.txt').write_text('ok')\n"
                "print(os.getcwd())\n"
                "print(os.getenv('OWA_SANDBOX_SHOULD_NOT_LEAK', 'missing'))\n"
            ),
        )
    )
    lines = result.stdout.splitlines()
    assert result.exit_code == 0
    assert lines[-1] == "missing"
    assert not Path(lines[0]).exists()
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_internal_script_resolves_only_approved_secret_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SANDBOX_TEST_SECRET", "expected")
    backend = InternalSandboxBackend(
        SandboxConfig(
            enabled=True,
            workspace_root=str(tmp_path / "sandbox"),
            secret_environment=["SANDBOX_TEST_SECRET"],
            memory_bytes=None,
        )
    )
    result = await backend.execute(
        SandboxExecutionRequest(
            execution_id="secret",
            kind="script",
            script_language="python",
            script_code="import os\nprint(os.getenv('TOKEN') == 'expected')\n",
            environment=(("TOKEN", SandboxSecretReference("SANDBOX_TEST_SECRET")),),
        )
    )
    assert result.stdout == "True\n"


@pytest.mark.asyncio
async def test_internal_sandbox_enforces_timeout_output_and_exit_status(tmp_path: Path) -> None:
    timeout_backend = InternalSandboxBackend(
        SandboxConfig(
            enabled=True,
            workspace_root=str(tmp_path / "timeout"),
            timeout_seconds=0.1,
            memory_bytes=None,
        )
    )
    with pytest.raises(SandboxTimeoutError):
        await timeout_backend.execute(
            SandboxExecutionRequest(
                execution_id="timeout",
                kind="script",
                script_language="python",
                script_code="import time\ntime.sleep(2)\n",
            )
        )

    output_backend = InternalSandboxBackend(
        SandboxConfig(
            enabled=True,
            workspace_root=str(tmp_path / "output"),
            max_output_bytes=128,
            memory_bytes=None,
        )
    )
    with pytest.raises(SandboxOutputLimitError):
        await output_backend.execute(
            SandboxExecutionRequest(
                execution_id="output",
                kind="script",
                script_language="python",
                script_code="print('x' * 4096)\n",
            )
        )

    exit_backend = InternalSandboxBackend(
        SandboxConfig(enabled=True, workspace_root=str(tmp_path / "exit"), memory_bytes=None)
    )
    with pytest.raises(SandboxProcessError) as error:
        await exit_backend.execute(
            SandboxExecutionRequest(
                execution_id="exit",
                kind="script",
                script_language="python",
                script_code="raise SystemExit(7)\n",
            )
        )
    assert error.value.details["exit_code"] == 7


@pytest.mark.asyncio
async def test_internal_sandbox_enforces_workspace_limit(tmp_path: Path) -> None:
    backend = InternalSandboxBackend(
        SandboxConfig(
            enabled=True,
            workspace_root=str(tmp_path / "workspace"),
            max_workspace_bytes=1024,
            memory_bytes=None,
        )
    )
    with pytest.raises(SandboxResourceLimitError):
        await backend.execute(
            SandboxExecutionRequest(
                execution_id="workspace",
                kind="script",
                script_language="python",
                script_code=(
                    "from pathlib import Path\n"
                    "import time\n"
                    "Path('large.bin').write_bytes(b'x' * 4096)\n"
                    "time.sleep(0.2)\n"
                ),
            )
        )


@pytest.mark.asyncio
async def test_cancelling_execution_terminates_and_cleans_workspace(tmp_path: Path) -> None:
    root = tmp_path / "cancel"
    backend = InternalSandboxBackend(
        SandboxConfig(enabled=True, workspace_root=str(root), memory_bytes=None)
    )
    task = asyncio.create_task(
        backend.execute(
            SandboxExecutionRequest(
                execution_id="cancelled",
                kind="script",
                script_language="python",
                script_code="import time\ntime.sleep(10)\n",
            )
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await backend.shutdown()
    assert not any(root.iterdir())


@pytest.mark.skipif(os.name != "posix", reason="POSIX resource limits are Linux/Unix specific")
def test_posix_capabilities_advertise_resource_limits(tmp_path: Path) -> None:
    backend = InternalSandboxBackend(
        SandboxConfig(enabled=True, workspace_root=str(tmp_path / "sandbox"))
    )
    capabilities = backend.capabilities()
    assert capabilities["resourceLimits"]["posixRlimit"] is True
    assert capabilities["hardIsolation"] is False
    assert capabilities["networkIsolation"] == "none"
