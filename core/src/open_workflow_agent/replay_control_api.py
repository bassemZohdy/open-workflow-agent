"""Original-owner public replay admission; execution continues in the new invocation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .engine import WorkflowEngine
from .errors import InvocationNotFound, UnsupportedWorkflowFeature
from .execution_authority import ExecutionAuthority, current_authority
from .native_extensions import LANGGRAPH_STATE_REPLAY as CAPABILITY
from .native_replay import NativeReplayController
from .persistence import ExecutionHandle
from .services import RuntimeServices


class ReplayStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    reference: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledge_reexecution: bool = False


def state_replay_capabilities() -> dict[str, Any]:
    return {
        "profile": CAPABILITY,
        "endpoint": "/v1/invocations/{invocation_id}/state-replays",
        "scope": "root",
        "checkpoints": "completed_task_prefix",
        "effectPolicies": ["read_only", "reexecute"],
        "response": "accepted_new_invocation",
        "actions": ["state.replay"],
        "branch": False,
        "timeTravel": False,
    }


def mount_state_replay(
    app: FastAPI,
    services: RuntimeServices,
    engine: WorkflowEngine,
    authorize: Callable[[str, str], None],
    invocation: Callable[[str, Request], ExecutionHandle],
) -> None:
    if services.config.native_state_replay is None:
        return

    def selected(
        invocation_id: str, request: Request
    ) -> tuple[ExecutionHandle, ExecutionAuthority, NativeReplayController]:
        handle = invocation(invocation_id, request)
        caller = current_authority()
        if caller is None or caller.principal.identity != handle.owner_principal:
            raise InvocationNotFound("invocation not found")
        resource = "invocation:" + invocation_id
        authorize("state.replay", resource)
        original = services.execution_authorizations.verify_invocation(handle)
        services.execution_authorizations.authorize_request(original, "state.replay", resource)
        controller = services.native_replay_controller
        if controller is None or CAPABILITY not in engine.capabilities().native_extensions:
            raise UnsupportedWorkflowFeature("native state replay is unavailable")
        controller.verify(services.config.native_state_replay)
        if request.query_params:
            raise UnsupportedWorkflowFeature("native state replay query parameters are unsupported")
        return handle, caller, controller

    @app.post("/v1/invocations/{invocation_id}/state-replays", status_code=202)
    async def replay(
        invocation_id: str,
        body: ReplayStateRequest,
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
            raise UnsupportedWorkflowFeature("one state replay idempotency key is required")
        request_id = hashlib.sha256(
            json.dumps([CAPABILITY, invocation_id, idempotency_key]).encode()
        ).hexdigest()
        result = await controller.admit(
            handle,
            caller,
            request_id=request_id,
            reference=body.reference,
            acknowledge_reexecution=body.acknowledge_reexecution,
        )
        selected(invocation_id, http_request)
        return JSONResponse(result, status_code=202)
