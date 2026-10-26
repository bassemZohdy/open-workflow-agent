"""Original-owner native state/history inspection. No execution or checkpoint writes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Query, Request

from .engine import WorkflowEngine
from .errors import InvocationNotFound, UnsupportedWorkflowFeature
from .execution_authority import current_authority
from .native_state import CAPABILITY, NativeStateQuery, NativeStateUnavailable
from .persistence import ExecutionHandle
from .services import RuntimeServices


def native_state_capabilities() -> dict[str, Any]:
    return {
        "profile": CAPABILITY,
        "stateEndpoint": "/v1/invocations/{invocation_id}/state",
        "historyEndpoint": "/v1/invocations/{invocation_id}/states",
        "scope": "root",
        "references": "opaque_invocation_and_definition_bound",
        "verification": "snapshot_and_immutable_receipt_bindings",
        "continuation": "not_inspected",
        "updates": False,
        "replay": False,
        "timeTravel": False,
        "action": "state.inspect",
    }


def mount_native_state(
    app: FastAPI,
    services: RuntimeServices,
    engine: WorkflowEngine,
    authorize: Callable[[str, str], None],
    invocation: Callable[[str, Request], ExecutionHandle],
) -> None:
    if services.config.native_state is None:
        return

    def owner(invocation_id: str, request: Request) -> ExecutionHandle:
        handle = invocation(invocation_id, request)
        caller = current_authority()
        if caller is None or caller.principal.identity != handle.owner_principal:
            raise InvocationNotFound("invocation not found")
        resource = "invocation:" + invocation_id
        authorize("state.inspect", resource)
        original = services.execution_authorizations.verify_invocation(handle)
        services.execution_authorizations.authorize_request(original, "state.inspect", resource)
        return handle

    async def inspect(invocation_id: str, request: Request, query: NativeStateQuery) -> Any:
        handle = owner(invocation_id, request)
        allowed = {"reference"} if query.kind == "state" else {"before", "limit"}
        if set(request.query_params) - allowed or any(
            len(request.query_params.getlist(key)) != 1 for key in request.query_params
        ):
            raise UnsupportedWorkflowFeature("native state query parameters are unsupported")
        configuration = services.config.native_state
        reader = services.native_state_reader
        if (
            configuration is None
            or reader is None
            or CAPABILITY not in engine.capabilities().native_extensions
        ):
            raise UnsupportedWorkflowFeature("native state views are unavailable")
        reader.verify(configuration)
        try:
            async with asyncio.timeout(configuration.timeout_seconds):
                plan = await services.recovery_definitions.resolve_inspection(handle)
                result = await reader.read(handle, plan, query)
                checked = owner(invocation_id, request)
                if checked.workflow_fingerprint != handle.workflow_fingerprint:
                    raise NativeStateUnavailable("native state definition changed during its read")
                reader.verify(services.config.native_state)
                return result.as_dict()
        except TimeoutError:
            raise NativeStateUnavailable("native state read timed out") from None

    @app.get("/v1/invocations/{invocation_id}/state")
    async def state(
        invocation_id: str,
        http_request: Request,
        reference: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"),
    ) -> Any:
        return await inspect(invocation_id, http_request, NativeStateQuery(reference=reference))

    @app.get("/v1/invocations/{invocation_id}/states")
    async def history(
        invocation_id: str,
        http_request: Request,
        before: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"),
        limit: int = Query(
            default=min(20, services.config.native_state.max_page_size), ge=1, le=100
        ),
    ) -> Any:
        return await inspect(
            invocation_id,
            http_request,
            NativeStateQuery(
                kind="history",
                before=before,
                limit=limit,
            ),
        )
