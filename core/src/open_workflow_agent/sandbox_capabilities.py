"""Backend-neutral sandbox requirements and capability compatibility checks.

This module defines the common execution contract used by stronger external
sandbox backends. It deliberately contains no Docker- or Kubernetes-native
identifiers so engine adapters and public lifecycle state remain portable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from .errors import SandboxPolicyError

SandboxExecutionKind = Literal["script", "shell", "container"]
SandboxFilesystemMode = Literal["workspace_cwd_only", "isolated_root"]
SandboxNetworkMode = Literal["unrestricted", "restricted", "denied"]


@dataclass(frozen=True, slots=True)
class SandboxInputFile:
    """Bounded input material to be staged inside a sandbox workspace."""

    path: str
    content: bytes
    executable: bool = False

    def __post_init__(self) -> None:
        normalized = PurePosixPath(self.path)
        if normalized.is_absolute() or not self.path or ".." in normalized.parts:
            raise ValueError("sandbox input file path must be relative and traversal-free")


@dataclass(frozen=True, slots=True)
class SandboxResourceRequirements:
    """Portable resource requirements; unset values mean no extra requirement."""

    timeout_seconds: float | None = None
    max_output_bytes: int | None = None
    max_workspace_bytes: int | None = None
    cpu_seconds: int | None = None
    memory_bytes: int | None = None
    file_size_bytes: int | None = None
    process_count: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("timeout_seconds", self.timeout_seconds),
            ("max_output_bytes", self.max_output_bytes),
            ("max_workspace_bytes", self.max_workspace_bytes),
            ("cpu_seconds", self.cpu_seconds),
            ("memory_bytes", self.memory_bytes),
            ("file_size_bytes", self.file_size_bytes),
            ("process_count", self.process_count),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be greater than zero when configured")


@dataclass(frozen=True, slots=True)
class SandboxIsolationRequirements:
    """Isolation guarantees requested by deployment policy for one execution."""

    filesystem: SandboxFilesystemMode = "workspace_cwd_only"
    network: SandboxNetworkMode = "unrestricted"
    hard_isolation: bool = False


@dataclass(frozen=True, slots=True)
class SandboxExecutionRequirements:
    """Backend-neutral execution requirements attached by deployment policy.

    Workflow authors do not select the backend. Runtime/image choice, staged
    files, and isolation/resource requirements are resolved by deployment
    policy before a backend receives the request.
    """

    kind: SandboxExecutionKind
    runtime: str | None = None
    image: str | None = None
    input_files: tuple[SandboxInputFile, ...] = ()
    requires_secret_references: bool = False
    resources: SandboxResourceRequirements = field(default_factory=SandboxResourceRequirements)
    isolation: SandboxIsolationRequirements = field(default_factory=SandboxIsolationRequirements)


@dataclass(frozen=True, slots=True)
class SandboxBackendCapabilities:
    """Portable backend capability descriptor without infrastructure-native state."""

    backend: str
    kinds: tuple[SandboxExecutionKind, ...]
    runtimes: tuple[str, ...] = ()
    supports_image_selection: bool = False
    supports_input_files: bool = False
    supports_secret_references: bool = False
    supports_cancellation: bool = True
    supports_cleanup: bool = True
    supports_timeout: bool = True
    supports_output_limit: bool = True
    supports_workspace_limit: bool = False
    supports_cpu_limit: bool = False
    supports_memory_limit: bool = False
    supports_file_size_limit: bool = False
    supports_process_limit: bool = False
    filesystem_modes: tuple[SandboxFilesystemMode, ...] = ("workspace_cwd_only",)
    network_modes: tuple[SandboxNetworkMode, ...] = ("unrestricted",)
    hard_isolation: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "kinds": list(self.kinds),
            "runtimes": list(self.runtimes),
            "imageSelection": self.supports_image_selection,
            "inputFiles": self.supports_input_files,
            "secretReferences": self.supports_secret_references,
            "cancellation": self.supports_cancellation,
            "cleanup": self.supports_cleanup,
            "resourceLimits": {
                "timeout": self.supports_timeout,
                "outputBytes": self.supports_output_limit,
                "workspaceBytes": self.supports_workspace_limit,
                "cpu": self.supports_cpu_limit,
                "memory": self.supports_memory_limit,
                "fileSize": self.supports_file_size_limit,
                "processCount": self.supports_process_limit,
            },
            "filesystemModes": list(self.filesystem_modes),
            "networkModes": list(self.network_modes),
            "hardIsolation": self.hard_isolation,
        }


def internal_backend_capabilities(
    runtime_capabilities: dict[str, object],
) -> SandboxBackendCapabilities:
    """Translate the current internal backend report into the common SPI descriptor."""

    if runtime_capabilities.get("backend") != "internal":
        raise ValueError("runtime capabilities do not describe the internal sandbox backend")
    script = runtime_capabilities.get("script")
    shell = runtime_capabilities.get("shell")
    container = runtime_capabilities.get("container")
    resource_limits = runtime_capabilities.get("resourceLimits")
    if not all(isinstance(value, dict) for value in (script, shell, container)):
        raise ValueError("runtime sandbox capabilities are incomplete")
    if not isinstance(resource_limits, dict):
        raise ValueError("runtime sandbox resource capabilities are incomplete")

    assert isinstance(script, dict)
    assert isinstance(shell, dict)
    assert isinstance(container, dict)
    kinds: list[SandboxExecutionKind] = []
    if script.get("enabled"):
        kinds.append("script")
    if shell.get("enabled"):
        kinds.append("shell")
    if container.get("enabled"):
        kinds.append("container")
    runtimes = script.get("runtimes", [])
    if not isinstance(runtimes, list) or any(not isinstance(item, str) for item in runtimes):
        raise ValueError("runtime sandbox runtimes are invalid")
    posix_limits = bool(resource_limits.get("posixRlimit"))
    return SandboxBackendCapabilities(
        backend="internal",
        kinds=tuple(kinds),
        runtimes=tuple(runtimes),
        supports_secret_references=True,
        supports_cancellation=bool(runtime_capabilities.get("cancellation")),
        supports_cleanup=True,
        supports_timeout=bool(resource_limits.get("timeout")),
        supports_output_limit=bool(resource_limits.get("outputBytes")),
        supports_workspace_limit=bool(resource_limits.get("workspaceQuota")),
        supports_cpu_limit=posix_limits,
        supports_memory_limit=posix_limits,
        supports_file_size_limit=posix_limits,
        supports_process_limit=posix_limits,
        filesystem_modes=("workspace_cwd_only",),
        network_modes=("unrestricted",),
        hard_isolation=bool(runtime_capabilities.get("hardIsolation")),
    )


def validate_backend_compatibility(
    capabilities: SandboxBackendCapabilities,
    requirements: SandboxExecutionRequirements,
) -> None:
    """Fail closed when the selected backend cannot satisfy deployment policy."""

    def reject(requirement: str) -> None:
        raise SandboxPolicyError(
            "selected sandbox backend cannot satisfy execution requirements",
            details={"backend": capabilities.backend, "requirement": requirement},
        )

    if requirements.kind not in capabilities.kinds:
        reject(f"kind:{requirements.kind}")
    if requirements.runtime is not None and requirements.runtime not in capabilities.runtimes:
        reject(f"runtime:{requirements.runtime}")
    if requirements.image is not None and not capabilities.supports_image_selection:
        reject("image_selection")
    if requirements.input_files and not capabilities.supports_input_files:
        reject("input_files")
    if requirements.requires_secret_references and not capabilities.supports_secret_references:
        reject("secret_references")
    if requirements.isolation.filesystem not in capabilities.filesystem_modes:
        reject(f"filesystem:{requirements.isolation.filesystem}")
    if requirements.isolation.network not in capabilities.network_modes:
        reject(f"network:{requirements.isolation.network}")
    if requirements.isolation.hard_isolation and not capabilities.hard_isolation:
        reject("hard_isolation")

    resource_requirements = (
        (requirements.resources.timeout_seconds, capabilities.supports_timeout, "timeout"),
        (
            requirements.resources.max_output_bytes,
            capabilities.supports_output_limit,
            "output_limit",
        ),
        (
            requirements.resources.max_workspace_bytes,
            capabilities.supports_workspace_limit,
            "workspace_limit",
        ),
        (requirements.resources.cpu_seconds, capabilities.supports_cpu_limit, "cpu_limit"),
        (requirements.resources.memory_bytes, capabilities.supports_memory_limit, "memory_limit"),
        (
            requirements.resources.file_size_bytes,
            capabilities.supports_file_size_limit,
            "file_size_limit",
        ),
        (
            requirements.resources.process_count,
            capabilities.supports_process_limit,
            "process_limit",
        ),
    )
    for value, supported, requirement in resource_requirements:
        if value is not None and not supported:
            reject(requirement)
