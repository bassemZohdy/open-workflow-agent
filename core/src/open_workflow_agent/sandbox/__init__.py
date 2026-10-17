"""Framework-neutral bounded sandbox execution.

Layers: the abstract contract and manager stay lean, the workflow-facing
capability gate and compilation live in `validation`, and the reusable
backend implementations (internal process machinery, restricted-controller
clients) live in `backends/` as shared utilities. Engines consume only the
public surface re-exported here.
"""

from __future__ import annotations

from .backends.internal import InternalSandboxBackend
from .contract import (
    SandboxBackend,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxKind,
    SandboxSecretReference,
    SandboxStatus,
    SandboxStdioRequest,
    SandboxStdioSession,
)
from .executor import SandboxWorkflowExecutor
from .manager import SandboxManager
from .stdio_policy import validate_stdio_transport
from .validation import (
    compile_sandbox_workflow,
    resolve_and_compile_sandbox_workflow,
    validate_sandbox_capabilities,
)

__all__ = [
    "InternalSandboxBackend",
    "SandboxBackend",
    "SandboxExecutionRequest",
    "SandboxExecutionResult",
    "SandboxManager",
    "SandboxSecretReference",
    "SandboxStatus",
    "SandboxKind",
    "SandboxStdioRequest",
    "SandboxStdioSession",
    "validate_stdio_transport",
    "SandboxWorkflowExecutor",
    "compile_sandbox_workflow",
    "resolve_and_compile_sandbox_workflow",
    "validate_sandbox_capabilities",
]
