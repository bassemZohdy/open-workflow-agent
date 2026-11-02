"""Authenticated original-owner admission of attributed state branches."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .engine import WorkflowEngine
from .errors import InvocationNotFound, ModelError, UnsupportedWorkflowFeature
from .execution_authority import ExecutionAuthority, current_authority
from .model_responses import bounded_model_json
from .native_branch import NativeBranchController
from .native_extensions import LANGGRAPH_STATE_BRANCH as CAPABILITY
from .persistence import ExecutionHandle
from .services import RuntimeServices


class BranchStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    reference: str = Field(pattern=r"^[a-f0-9]{64}$")
    task_reference: str = Field(min_length=1, max_length=4096)
    values: dict[str, Any]
    acknowledge_reexecution: bool = False

    @field_validator("values")
    @classmethod
    def bounded_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            bounded_model_json(value, limit=65_536)
        except ModelError:
            raise ValueError("state branch values must be finite bounded JSON") from None
        return value


def state_branch_capabilities() -> dict[str, Any]:
    return {
        "profile": CAPABILITY,
        "endpoint": "/v1/invocations/{invocation_id}/state-branches",
        "scope": "root",
        "checkpoints": "completed_task_prefix",
        "attribution": "last_completed_declared_set_task",
        "effectPolicies": ["read_only", "reexecute"],
        "response": "accepted_new_invocation",
        "actions": ["state.branch"],
        "timeTravel": False,
    }


def mount_state_branch(
    app: FastAPI,
    services: RuntimeServices,
    engine: WorkflowEngine,
    authorize: Callable[[str, str], None],
    invocation: Callable[[str, Request], ExecutionHandle],
) -> None:
    if services.config.native_state_branch is None:
        return

    def selected(
        invocation_id: str, request: Request
    ) -> tuple[ExecutionHandle, ExecutionAuthority, NativeBranchController]:
        handle = invocation(invocation_id, request)
        caller = current_authority()
        if caller is None or caller.principal.identity != handle.owner_principal:
            raise InvocationNotFound("invocation not found")
        resource = "invocation:" + invocation_id
        authorize("state.branch", resource)
        original = services.execution_authorizations.verify_invocation(handle)
        services.execution_authorizations.authorize_request(original, "state.branch", resource)
        controller = services.native_branch_controller
        if controller is None or CAPABILITY not in engine.capabilities().native_extensions:
            raise UnsupportedWorkflowFeature("native state branch is unavailable")
        controller.verify(services.config.native_state_branch)
        if request.query_params:
            raise UnsupportedWorkflowFeature("native state branch query parameters are unsupported")
        return handle, caller, controller

    @app.post("/v1/invocations/{invocation_id}/state-branches", status_code=202)
    async def branch(
        invocation_id: str,
        body: BranchStateRequest,
        http_request: Request,
        idempotency_key: str = Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        ),
    ) -> JSONResponse:
        handle, caller, controller = selected(invocation_id, http_request)
        if len(http_request.headers.getlist("Idempotency-Key")) != 1:
            raise UnsupportedWorkflowFeature("one state branch idempotency key is required")
        request_id = hashlib.sha256(
            json.dumps([CAPABILITY, invocation_id, idempotency_key]).encode()
        ).hexdigest()
        result = await controller.admit(
            handle,
            caller,
            request_id=request_id,
            reference=body.reference,
            task_reference=body.task_reference,
            values=body.values,
            acknowledge_reexecution=body.acknowledge_reexecution,
        )
        selected(invocation_id, http_request)
        return JSONResponse(result, status_code=202)
