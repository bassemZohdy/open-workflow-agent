from __future__ import annotations

from pathlib import Path

import pytest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.errors import SandboxPolicyError
from open_workflow_agent.sandbox import InternalSandboxBackend
from open_workflow_agent.sandbox_capabilities import (
    SandboxBackendCapabilities,
    SandboxExecutionRequirements,
    SandboxInputFile,
    SandboxIsolationRequirements,
    SandboxResourceRequirements,
    internal_backend_capabilities,
    validate_backend_compatibility,
)


def test_input_files_are_relative_and_traversal_free() -> None:
    SandboxInputFile(path="input/config.json", content=b"{}")
    with pytest.raises(ValueError, match="relative"):
        SandboxInputFile(path="/etc/passwd", content=b"")
    with pytest.raises(ValueError, match="traversal"):
        SandboxInputFile(path="../secret", content=b"")


def test_internal_backend_maps_to_honest_portable_capabilities(tmp_path: Path) -> None:
    backend = InternalSandboxBackend(
        SandboxConfig(
            enabled=True,
            allow_shell=True,
            workspace_root=str(tmp_path / "sandbox"),
            memory_bytes=None,
        )
    )
    capabilities = internal_backend_capabilities(backend.capabilities())

    assert capabilities.backend == "internal"
    assert capabilities.kinds == ("script", "shell")
    assert capabilities.runtimes == ("python",)
    assert capabilities.supports_image_selection is False
    assert capabilities.supports_input_files is False
    assert capabilities.supports_secret_references is True
    assert capabilities.filesystem_modes == ("workspace_cwd_only",)
    assert capabilities.network_modes == ("unrestricted",)
    assert capabilities.hard_isolation is False
    assert "runner_id" not in capabilities.as_dict()
    assert "container_id" not in capabilities.as_dict()
    assert "pod_name" not in capabilities.as_dict()


def test_internal_backend_accepts_only_requirements_it_can_enforce(tmp_path: Path) -> None:
    backend = InternalSandboxBackend(
        SandboxConfig(enabled=True, workspace_root=str(tmp_path / "sandbox"), memory_bytes=None)
    )
    capabilities = internal_backend_capabilities(backend.capabilities())

    validate_backend_compatibility(
        capabilities,
        SandboxExecutionRequirements(
            kind="script",
            runtime="python",
            requires_secret_references=True,
            resources=SandboxResourceRequirements(
                timeout_seconds=5,
                max_output_bytes=1024,
                max_workspace_bytes=4096,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("requirements", "expected_requirement"),
    [
        (
            SandboxExecutionRequirements(kind="container", image="registry.example/app@sha256:abc"),
            "kind:container",
        ),
        (
            SandboxExecutionRequirements(
                kind="script",
                runtime="python",
                input_files=(SandboxInputFile(path="input.txt", content=b"x"),),
            ),
            "input_files",
        ),
        (
            SandboxExecutionRequirements(
                kind="script",
                runtime="python",
                isolation=SandboxIsolationRequirements(hard_isolation=True),
            ),
            "hard_isolation",
        ),
        (
            SandboxExecutionRequirements(
                kind="script",
                runtime="python",
                isolation=SandboxIsolationRequirements(network="denied"),
            ),
            "network:denied",
        ),
        (
            SandboxExecutionRequirements(
                kind="script",
                runtime="python",
                isolation=SandboxIsolationRequirements(filesystem="isolated_root"),
            ),
            "filesystem:isolated_root",
        ),
    ],
)
def test_internal_backend_fails_closed_for_stronger_requirements(
    tmp_path: Path,
    requirements: SandboxExecutionRequirements,
    expected_requirement: str,
) -> None:
    backend = InternalSandboxBackend(
        SandboxConfig(enabled=True, workspace_root=str(tmp_path / "sandbox"), memory_bytes=None)
    )
    capabilities = internal_backend_capabilities(backend.capabilities())

    with pytest.raises(SandboxPolicyError) as error:
        validate_backend_compatibility(capabilities, requirements)
    assert error.value.details == {
        "backend": "internal",
        "requirement": expected_requirement,
    }


def test_external_backend_contract_can_describe_container_grade_isolation() -> None:
    capabilities = SandboxBackendCapabilities(
        backend="docker",
        kinds=("script", "shell", "container"),
        runtimes=("python",),
        supports_image_selection=True,
        supports_input_files=True,
        supports_secret_references=True,
        supports_workspace_limit=True,
        supports_cpu_limit=True,
        supports_memory_limit=True,
        supports_file_size_limit=True,
        supports_process_limit=True,
        filesystem_modes=("isolated_root",),
        network_modes=("restricted", "denied"),
        hard_isolation=True,
    )
    requirements = SandboxExecutionRequirements(
        kind="container",
        image="registry.example/app@sha256:0123456789abcdef",
        input_files=(SandboxInputFile(path="input/request.json", content=b"{}"),),
        requires_secret_references=True,
        resources=SandboxResourceRequirements(
            timeout_seconds=30,
            max_output_bytes=1_048_576,
            max_workspace_bytes=16_777_216,
            cpu_seconds=10,
            memory_bytes=268_435_456,
            file_size_bytes=8_388_608,
            process_count=32,
        ),
        isolation=SandboxIsolationRequirements(
            filesystem="isolated_root",
            network="denied",
            hard_isolation=True,
        ),
    )

    validate_backend_compatibility(capabilities, requirements)
