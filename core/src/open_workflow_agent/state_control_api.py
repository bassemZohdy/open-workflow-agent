"""Authenticated original-owner state update and recovery ingress."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Literal

from fastapi import FastAPI, Header, Path, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .engine import WorkflowEngine
from .errors import InvocationNotFound, InvocationStateError, ModelError, UnsupportedWorkflowFeature
from .execution_authority import ExecutionAuthority, current_authority
from .model_responses import bounded_model_json
from .native_state_policy import CAPABILITY
from .persistence import ExecutionHandle
from .services import RuntimeServices
from .state_control import NativeStateController
from .state_update_resolution import StateUpdateResolution


class UpdateStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    base_reference: str = Field(pattern=r"^[a-f0-9]{64}$")
    task_reference: str = Field(min_length=1, max_length=4096)
    values: dict[str, Any]

    @field_validator("values")
    @classmethod
    def bounded_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            bounded_model_json(value, limit=65_536)
        except ModelError:
            raise ValueError("state update values must be finite bounded JSON") from None
        return value


class ResolveStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["complete", "abandon_unknown"]
    acknowledge_unknown: bool = False


def state_control_capabilities() -> dict[str, Any]:
    return {
        "profile": CAPABILITY,
        "endpoint": "/v1/invocations/{invocation_id}/state-updates",
        "resolutionEndpoint": (
            "/v1/invocations/{invocation_id}/state-updates/{operation_id}/resolve"
        ),
        "scope": "root",
        "attribution": "declared_previously_visited_set_task",
        "actions": ["state.update", "state.inspect", "state.resolve"],
        "replay": False,
        "timeTravel": False,
    }


def mount_state_controls(
    app: FastAPI,
    services: RuntimeServices,
    engine: WorkflowEngine,
    authorize: Callable[[str, str], None],
    invocation: Callable[[str, Request], ExecutionHandle],
) -> None:
    if services.config.native_state_updates is None:
        return

    def selected(
        invocation_id: str, request: Request, action: str
    ) -> tuple[ExecutionHandle, ExecutionAuthority, NativeStateController]:
        handle = invocation(invocation_id, request)
        caller = current_authority()
        if caller is None or caller.principal.identity != handle.owner_principal:
            raise InvocationNotFound("invocation not found")
        resource = "invocation:" + invocation_id
        authorize(action, resource)
        original = services.execution_authorizations.verify_invocation(handle)
        services.execution_authorizations.authorize_request(original, action, resource)
        controller = services.native_state_controller
        if controller is None or CAPABILITY not in engine.capabilities().native_extensions:
            raise UnsupportedWorkflowFeature("native state updates are unavailable")
        controller.verify(services.config.native_state_updates)
        if request.query_params:
            raise UnsupportedWorkflowFeature(
                "native state control query parameters are unsupported"
            )
        return handle, caller, controller

    @app.post("/v1/invocations/{invocation_id}/state-updates")
    async def update(
        invocation_id: str,
        body: UpdateStateRequest,
        http_request: Request,
        idempotency_key: str = Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        ),
    ) -> dict[str, Any]:
        handle, caller, controller = selected(invocation_id, http_request, "state.update")
        if len(http_request.headers.getlist("Idempotency-Key")) != 1:
            raise UnsupportedWorkflowFeature("one state control idempotency key is required")
        bounded_model_json(body.values, limit=65_536)
        request_id = hashlib.sha256(
            json.dumps([CAPABILITY, invocation_id, idempotency_key]).encode()
        ).hexdigest()
        result = await controller.update(
            handle,
            caller,
            request_id=request_id,
            base_reference=body.base_reference,
            task_reference=body.task_reference,
            values=body.values,
        )
        selected(invocation_id, http_request, "state.update")
        return result

    @app.get("/v1/invocations/{invocation_id}/state-updates")
    async def inspect(invocation_id: str, http_request: Request) -> dict[str, Any]:
        handle, caller, controller = selected(invocation_id, http_request, "state.inspect")
        result = await controller.inspect(handle, caller)
        selected(invocation_id, http_request, "state.inspect")
        return result

    @app.post("/v1/invocations/{invocation_id}/state-updates/{operation_id}/resolve")
    async def resolve(
        invocation_id: str,
        body: ResolveStateRequest,
        http_request: Request,
        operation_id: str = Path(pattern=r"^[a-f0-9]{64}$"),
        idempotency_key: str = Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        ),
    ) -> dict[str, Any]:
        handle, caller, controller = selected(invocation_id, http_request, "state.resolve")
        if len(http_request.headers.getlist("Idempotency-Key")) != 1:
            raise UnsupportedWorkflowFeature("one state control idempotency key is required")
        try:
            decision = StateUpdateResolution(
                idempotency_key, caller.principal.identity, body.decision, body.acknowledge_unknown
            )
        except ValueError:
            raise InvocationStateError(
                "state resolution requires an explicit valid decision"
            ) from None
        result = await controller.resolve(
            handle,
            caller,
            operation_id=operation_id,
            request_digest=body.request_digest,
            decision=decision,
        )
        selected(invocation_id, http_request, "state.resolve")
        return result
