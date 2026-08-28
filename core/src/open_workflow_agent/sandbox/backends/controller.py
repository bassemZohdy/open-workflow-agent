"""Shared restricted-controller client utilities for external sandbox backends.

The Docker and Kubernetes backends talk to different controllers over
different transports, but the error mapping, cancellation semantics, and
acceptable success codes are one reusable contract.
"""

from __future__ import annotations

import httpx

from ...errors import (
    SandboxOutputLimitError,
    SandboxPolicyError,
    SandboxProcessError,
    SandboxResourceLimitError,
    SandboxTimeoutError,
)

ERROR_TYPES = {
    "sandbox_policy_error": SandboxPolicyError,
    "sandbox_timeout": SandboxTimeoutError,
    "sandbox_output_limit": SandboxOutputLimitError,
    "sandbox_resource_limit": SandboxResourceLimitError,
    "sandbox_process_error": SandboxProcessError,
}


def controller_error(response: httpx.Response, *, backend_label: str) -> Exception:
    code = "sandbox_process_error"
    try:
        body = response.json()
        error = body.get("error", {}) if isinstance(body, dict) else {}
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            code = error["code"]
    except ValueError:
        pass
    error_type = ERROR_TYPES.get(code, SandboxProcessError)
    return error_type(
        f"{backend_label} sandbox controller rejected execution",
        details={"controller_code": code, "status": response.status_code},
    )


async def cancel_controller_execution(
    client: httpx.AsyncClient,
    *,
    backend_label: str,
    execution_id: str,
) -> None:
    try:
        response = await client.delete(f"/v1/executions/{execution_id}")
    except httpx.HTTPError as exc:
        raise SandboxProcessError(f"{backend_label} sandbox cancellation request failed") from exc
    if response.status_code not in {200, 202, 204, 404}:
        raise controller_error(response, backend_label=backend_label)
