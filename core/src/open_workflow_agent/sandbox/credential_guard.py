"""Direct literal output guard from the actual common process environment."""

from __future__ import annotations

from collections.abc import Mapping

from ..credential_guard import LiteralCredentialGuard, capture_credentials
from ..errors import SandboxPolicyError, ToolError
from .contract import SandboxExecutionRequest, SandboxSecretReference


def process_credential_guard(
    request: SandboxExecutionRequest, environment: Mapping[str, str]
) -> LiteralCredentialGuard:
    guard = LiteralCredentialGuard()
    try:
        values = [
            environment[name]
            for name, reference in request.environment
            if isinstance(reference, SandboxSecretReference)
        ]
        values.extend(value.replace("\r\n", "\n") for value in tuple(values) if "\r\n" in value)
        guard.capture(values)
        capture_credentials(*values)
    except (KeyError, ToolError):
        raise SandboxPolicyError("sandbox credential output guard binding is unavailable") from None
    return guard
