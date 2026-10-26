"""Attested OWA data replacement through the pinned native SDK updater."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from importlib import import_module
from typing import Any, cast

from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.state_updates import (
    StateUpdateProof,
    StateUpdateRequest,
    state_update_error,
)
from open_workflow_agent.workflow import ExecutionState, WorkflowExecutor, WorkflowPlan

from .graph import LangGraphScopeExecutor, _json_identity
from .native_state_controls import LangGraphStateControls
from .native_state_preflight import NativeStatePreflight
from .native_state_storage import guard_checkpoint_sync

_FIELDS = ("snapshot", "counters", "next_node", "receipt_bindings")


def projection(native: dict[str, Any]) -> dict[str, Any]:
    value = {key: native[key] for key in _FIELDS}
    if "native_channels" in native:
        value["native_channels"] = native["native_channels"]
    return value


def projection_digest(native: dict[str, Any]) -> str:
    return hashlib.sha256(_json_identity(projection(native)).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class NativeStateTransition:
    preflight: NativeStatePreflight
    request: StateUpdateRequest
    native_json: str


def plan_state_transition(preflight: NativeStatePreflight) -> NativeStateTransition:
    native = projection(json.loads(preflight.base_native_json))
    snapshot = json.loads(native["snapshot"])
    snapshot["data"] = preflight.request.values
    native["snapshot"] = _json_identity(snapshot)
    bindings = json.loads(native["receipt_bindings"])
    if bindings["version"] not in {2, 3}:
        raise state_update_error("native_binding_version_unsupported")
    controls = bindings.get("controls", [])
    if (
        not isinstance(controls, list)
        or len(controls) >= 64
        or preflight.request.request_id in controls
    ):
        raise state_update_error("control_lineage_invalid")
    bindings["version"] = 3
    bindings["controls"] = [*controls, preflight.request.request_id]
    native["receipt_bindings"] = _json_identity(bindings)
    bindings["state_digest"] = LangGraphScopeExecutor._state_digest(native)
    native["receipt_bindings"] = _json_identity(bindings)
    request = replace(
        preflight.request,
        base_digest=preflight.base_digest,
        projection_digest=projection_digest(native),
        previous_controls=tuple(controls),
    )
    return NativeStateTransition(preflight, request, _json_identity(native))


def verify_projection_sync(
    connection: Any,
    postgres: bool,
    thread: str,
    checkpoint_id: str,
    request: StateUpdateRequest,
    budget: int,
) -> None:
    guard_checkpoint_sync(connection, thread, checkpoint_id, budget, postgres)
    sdk = import_module(
        "langgraph.checkpoint.postgres" if postgres else "langgraph.checkpoint.sqlite"
    )
    saver = sdk.PostgresSaver(connection) if postgres else sdk.SqliteSaver(connection)
    if not postgres:
        saver.is_setup = True
    checkpoint = saver.get_tuple(
        {
            "configurable": {
                "thread_id": thread,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        }
    )
    if (
        checkpoint is None
        or checkpoint.checkpoint.get("id") != checkpoint_id
        or checkpoint.checkpoint.get("v") != 4
        or checkpoint.metadata.get("source") != "update"
        or projection_digest(checkpoint.checkpoint["channel_values"]) != request.projection_digest
    ):
        raise state_update_error("native_attestation_changed")


class AttestedStateControls(LangGraphStateControls):
    """Stage immutable preflight results; only an installed coordinator dispatches them."""

    def __init__(
        self,
        services: RuntimeServices,
        plan: WorkflowPlan,
        *,
        bounds: NativeStateConfig | None = None,
    ) -> None:
        self.proposals: dict[str, NativeStateTransition] = {}
        super().__init__(services, plan, self._update_owned, bounds=bounds)

    def stage(self, preflight: NativeStatePreflight) -> NativeStateTransition:
        self._verify(preflight.request)
        candidate = plan_state_transition(preflight)
        previous = self.proposals.get(candidate.request.identity.key)
        if previous is not None and previous != candidate:
            raise state_update_error("staged_control_changed")
        if previous is None and len(self.proposals) >= 64:
            raise state_update_error("staged_control_limit")
        retained = sum(
            self._size(item)
            for key, item in self.proposals.items()
            if key != candidate.request.identity.key
        )
        if retained + self._size(candidate) > min(67_108_864, self.bounds.max_native_bytes * 4):
            raise state_update_error("staged_control_bytes")
        self.proposals[candidate.request.identity.key] = candidate
        return candidate

    @staticmethod
    def _size(candidate: NativeStateTransition) -> int:
        return len(candidate.preflight.base_native_json.encode()) + len(
            candidate.native_json.encode()
        )

    def inspect(self, request: StateUpdateRequest) -> StateUpdateProof | None:
        proof = super().inspect(request)
        if proof is not None:
            self.proposals.pop(request.identity.key, None)
        return proof

    async def _update_owned(
        self, saver: Any, config: dict[str, Any], request: StateUpdateRequest
    ) -> dict[str, Any]:
        candidate = self.proposals.get(request.identity.key)
        if candidate is None or candidate.request != request:
            raise state_update_error("staged_control_missing")
        preflight = candidate.preflight
        # This callback runs with the native/common ownership lock held. No
        # common store is read here and no OWA task/model/tool is executed.
        driver = LangGraphScopeExecutor(self.plan, saver, request.invocation_id)
        executor = WorkflowExecutor(self.services.catalog, services=self.services)
        graph = driver.compile_scope(self.plan.scopes[0], executor, ExecutionState(data=None))
        result = await graph.aupdate_state(
            config, json.loads(candidate.native_json), as_node=preflight.task_node
        )
        return cast(dict[str, Any], result)
