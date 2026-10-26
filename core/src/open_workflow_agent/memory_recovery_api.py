"""Authenticated original-owner controls over common native-memory write IDs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import FastAPI, Header, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from .effects import ReceiptDependency, effect_request_digest
from .engine import WorkflowEngine
from .errors import InvocationNotFound, InvocationStateError, UnsupportedWorkflowFeature
from .execution_authority import current_authority
from .execution_authorization import decision_identity
from .memory_mutations import MemoryMutation
from .memory_resolution import MemoryWriteResolution, resolution_identity, resolution_receipt
from .native_memory_receipts import verify_mutation_parent
from .persistence import ExecutionHandle
from .services import RuntimeServices


class ResolveMemoryWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["complete", "abandon_unknown"]
    acknowledge_unknown: bool = False


def mount_memory_recovery(
    app: FastAPI,
    services: RuntimeServices,
    engine: WorkflowEngine,
    authorize: Callable[[str, str], None],
    invocation: Callable[[str, Request], ExecutionHandle],
) -> None:
    if not services.config.memory.native:
        return

    def requests(handle: ExecutionHandle) -> list[tuple[MemoryMutation, str]]:
        guard = services.native_memory_call_guard
        if guard is None:
            raise UnsupportedWorkflowFeature("native memory recovery controls are unavailable")
        for binding in services.config.memory.native:
            guard(f"memory.put:1.0.0@{binding.id}")
        allowed = {
            (binding.id, binding.version, binding.collection)
            for binding in services.config.memory.native
        }
        return [
            (request, phase)
            for request, phase in services.memory_writes.requests(handle)
            if (request.binding.id, request.binding.version, request.collection) in allowed
        ]

    def authority(handle: ExecutionHandle, action: str) -> Any:
        caller = current_authority()
        if caller is None or caller.principal.identity != handle.owner_principal:
            raise InvocationNotFound("invocation not found")
        authorize(action, "invocation:" + handle.invocation_id)
        original = services.execution_authorizations.verify_invocation(handle)
        if action == "memory.resolve":
            services.execution_authorizations.authorize_request(original, action, "memory:self")
        return caller

    @app.get("/v1/invocations/{invocation_id}/memory-writes")
    async def inspect(invocation_id: str, http_request: Request) -> dict[str, Any]:
        handle = invocation(invocation_id, http_request)
        authority(handle, "memory.inspect")
        return {
            "profile": "owa/native-memory-recovery/v1",
            "invocation_id": invocation_id,
            "verification": "common_metadata_only",
            "writes": [
                {
                    "operation_id": request.identity.key,
                    "request_digest": request.request_digest,
                    "task_reference": request.parent.task_reference,
                    "occurrence": [list(item) for item in request.parent.occurrence],
                    "binding": request.binding.id,
                    "key": request.key,
                    "kind": request.kind,
                    "state": "completed" if phase == "resolved_complete" else phase,
                }
                for request, phase in requests(handle)
            ],
        }

    @app.post("/v1/invocations/{invocation_id}/memory-writes/{operation_id}/resolve")
    async def resolve(
        invocation_id: str,
        request: ResolveMemoryWriteRequest,
        http_request: Request,
        operation_id: str = Path(pattern=r"^[a-f0-9]{64}$"),
        idempotency_key: str = Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        ),
    ) -> dict[str, Any]:
        handle = invocation(invocation_id, http_request)
        caller = authority(handle, "memory.resolve")
        target = next(
            (item for item, _phase in requests(handle) if item.identity.key == operation_id), None
        )
        if target is None:
            raise InvocationNotFound("memory write not found")
        if target.request_digest != request.request_digest:
            raise InvocationStateError("memory resolution request differs from the retained intent")
        parent = services.effect_journal.get(target.parent)
        try:
            if parent is None:
                raise ValueError("missing parent")
            verify_mutation_parent(target, parent)
        except ValueError:
            raise InvocationStateError(
                "memory resolution has no matching catalog request"
            ) from None
        try:
            decision = MemoryWriteResolution(
                idempotency_key,
                caller.principal.identity,
                request.decision,
                request.acknowledge_unknown,
            )
        except ValueError:
            raise InvocationStateError(
                "memory resolution requires an explicit valid decision"
            ) from None
        selected = resolution_identity(target.identity)
        permission = services.effect_journal.get(
            decision_identity(selected, "memory.resolve", "memory:self")
        )
        # A committed decision may be acknowledged again after workflow completion;
        # no terminal invocation is reopened and no new authority record is made.
        if handle.status not in {"running", "waiting"}:
            if permission is None:
                raise InvocationStateError("terminal invocations cannot admit memory resolutions")
            decision = MemoryWriteResolution(
                idempotency_key,
                caller.principal.identity,
                request.decision,
                request.acknowledge_unknown,
                ReceiptDependency._digest(permission),
            )
            audit = services.effect_journal.get(selected)
            backend = services.memory_writes._backend(target)
            if audit != resolution_receipt(target, backend.storage_identity, decision):
                raise InvocationStateError(
                    "terminal memory resolution differs from the recorded decision"
                )
        else:
            async with engine._own_execution(handle):
                services.invocations.refresh(handle)
                granted = services.execution_authorizations.authorize(
                    handle,
                    selected,
                    "memory.resolve",
                    "memory:self",
                    effect_request_digest(
                        "owa/action-permission/v1",
                        {
                            "identity": selected.key,
                            "action": "memory.resolve",
                            "resource": "memory:self",
                        },
                    ),
                    execution_lease=handle._execution_lease,
                )
                if granted is None:
                    raise InvocationStateError("memory resolution requires retained authorization")
                decision = MemoryWriteResolution(
                    idempotency_key,
                    caller.principal.identity,
                    request.decision,
                    request.acknowledge_unknown,
                    ReceiptDependency._digest(granted.receipt),
                )
                audit = await services.memory_writes.resolve(handle, target, decision)
        assert audit is not None
        return {
            "invocation_id": invocation_id,
            "operation_id": operation_id,
            "decision": decision.decision,
            "resolution_digest": audit.request_digest,
        }
