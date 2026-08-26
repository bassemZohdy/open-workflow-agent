from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import (
    SandboxOutputLimitError,
    SandboxPolicyError,
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
from pydantic import ValidationError


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
async def test_runtime_generated_errors_do_not_echo_approved_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sandbox-secret-value-that-must-not-be-logged"
    monkeypatch.setenv("SANDBOX_TEST_SECRET", secret)
    backend = InternalSandboxBackend(
        SandboxConfig(
            enabled=True,
            allow_shell=True,
            workspace_root=str(tmp_path / "sandbox"),
            secret_environment=["SANDBOX_TEST_SECRET"],
            memory_bytes=None,
        )
    )
    with pytest.raises(SandboxProcessError) as error:
        await backend.execute(
            SandboxExecutionRequest(
                execution_id="secret-error",
                kind="shell",
                command="false",
                environment=(("TOKEN", SandboxSecretReference("SANDBOX_TEST_SECRET")),),
            )
        )
    assert secret not in str(error.value)
    assert secret not in repr(error.value.details)


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
async def test_internal_sandbox_rejects_invalid_runtime_and_executable(tmp_path: Path) -> None:
    backend = InternalSandboxBackend(
        SandboxConfig(
            enabled=True,
            allow_shell=True,
            workspace_root=str(tmp_path / "sandbox"),
            memory_bytes=None,
        )
    )
    with pytest.raises(SandboxPolicyError, match="runtime"):
        await backend.execute(
            SandboxExecutionRequest(
                execution_id="runtime",
                kind="script",
                script_language="node",
                script_code="console.log('x')",
            )
        )
    with pytest.raises(SandboxPolicyError, match="not available"):
        await backend.execute(
            SandboxExecutionRequest(
                execution_id="executable",
                kind="shell",
                command="owa-command-that-does-not-exist",
            )
        )


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


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX specific")
async def test_cancellation_terminates_descendant_process_tree(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    pid_file = tmp_path / "child.pid"
    backend = InternalSandboxBackend(
        SandboxConfig(enabled=True, workspace_root=str(root), memory_bytes=None)
    )
    task = asyncio.create_task(
        backend.execute(
            SandboxExecutionRequest(
                execution_id="tree",
                kind="script",
                script_language="python",
                arguments=(str(pid_file),),
                script_code=(
                    "import subprocess, sys, time\n"
                    "from pathlib import Path\n"
                    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                    "Path(sys.argv[1]).write_text(str(child.pid))\n"
                    "time.sleep(30)\n"
                ),
            )
        )
    )
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.02)
    assert pid_file.exists()
    child_pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(100):
        stat = Path(f"/proc/{child_pid}/stat")
        if not stat.exists():
            break
        try:
            state = stat.read_text().split()[2]
        except FileNotFoundError:
            break
        if state == "Z":
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("sandbox descendant process remained running after cancellation")
    assert not any(root.iterdir())


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="file descriptor inheritance is POSIX specific")
async def test_child_does_not_inherit_parent_file_descriptors(tmp_path: Path) -> None:
    inherited = tmp_path / "parent-only.txt"
    with inherited.open("w+") as handle:
        os.set_inheritable(handle.fileno(), True)
        backend = InternalSandboxBackend(
            SandboxConfig(
                enabled=True,
                workspace_root=str(tmp_path / "sandbox"),
                memory_bytes=None,
            )
        )
        result = await backend.execute(
            SandboxExecutionRequest(
                execution_id="fd",
                kind="script",
                script_language="python",
                arguments=(str(handle.fileno()),),
                script_code=(
                    "import os, sys\n"
                    "try:\n"
                    "    os.fstat(int(sys.argv[1]))\n"
                    "except OSError:\n"
                    "    print('closed')\n"
                    "else:\n"
                    "    print('inherited')\n"
                ),
            )
        )
    assert result.stdout == "closed\n"


@pytest.mark.asyncio
async def test_concurrent_success_and_failure_cleanup(tmp_path: Path) -> None:
    root = tmp_path / "concurrent"
    backend = InternalSandboxBackend(
        SandboxConfig(enabled=True, workspace_root=str(root), memory_bytes=None)
    )
    results = await asyncio.gather(
        backend.execute(
            SandboxExecutionRequest(
                execution_id="ok",
                kind="script",
                script_language="python",
                script_code="print('ok')\n",
            )
        ),
        backend.execute(
            SandboxExecutionRequest(
                execution_id="fail",
                kind="script",
                script_language="python",
                script_code="raise SystemExit(9)\n",
            )
        ),
        return_exceptions=True,
    )
    assert results[0].stdout == "ok\n"  # type: ignore[union-attr]
    assert isinstance(results[1], SandboxProcessError)
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX resource limits are Linux/Unix specific")
async def test_posix_cpu_limit_terminates_runaway_process(tmp_path: Path) -> None:
    backend = InternalSandboxBackend(
        SandboxConfig(
            enabled=True,
            workspace_root=str(tmp_path / "sandbox"),
            timeout_seconds=5,
            cpu_seconds=1,
            memory_bytes=None,
        )
    )
    with pytest.raises(SandboxResourceLimitError):
        await backend.execute(
            SandboxExecutionRequest(
                execution_id="cpu-limit",
                kind="script",
                script_language="python",
                script_code="while True:\n    pass\n",
            )
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX resource limits are Linux/Unix specific")
def test_posix_capabilities_advertise_resource_limits(tmp_path: Path) -> None:
    backend = InternalSandboxBackend(
        SandboxConfig(enabled=True, workspace_root=str(tmp_path / "sandbox"))
    )
    capabilities = backend.capabilities()
    assert capabilities["resourceLimits"]["posixRlimit"] is True
    assert capabilities["hardIsolation"] is False
    assert capabilities["filesystemIsolation"] == "workspace_cwd_only"
    assert capabilities["networkIsolation"] == "none"
