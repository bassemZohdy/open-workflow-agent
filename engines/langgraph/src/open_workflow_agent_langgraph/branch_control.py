"""Bounded original-owner admission for attributed native branches."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from open_workflow_agent.config import NativeStateBranchConfig
from open_workflow_agent.errors import (
    ConfigurationError,
    InvocationStateError,
    UnsupportedWorkflowFeature,
)
from open_workflow_agent.execution_authority import ExecutionAuthority
from open_workflow_agent.native_state import NativeStateCapacity
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.state_lineage import lineage_error
from open_workflow_agent.storage import is_postgresql_datasource

from .native_replay_coordinator import NativeBranchCoordinator
from .replay_control import _consume

if TYPE_CHECKING:
    from open_workflow_agent.services import RuntimeServices

    from . import LangGraphWorkflowEngine


@dataclass(slots=True)
class _BranchFlight:
    task: asyncio.Task[Any]
    bound: asyncio.Future[str]
    target: dict[str, str]
    source_id: str
    reference: str
    task_reference: str
    values_json: str
    acknowledge_reexecution: bool


class LangGraphBranchController:
    """Admit once, then continue a lineage-bound target in a tracked task."""

    def __init__(self, services: RuntimeServices, engine: LangGraphWorkflowEngine) -> None:
        config = services.config.native_state_branch
        if config is None:
            raise ConfigurationError("native state branch is not configured")
        self.services, self.engine = services, engine
        self.config = NativeStateBranchConfig.model_validate(config.model_dump())
        self._coordinator: NativeBranchCoordinator | None = None
        self.path = ""
        self.opened = False
        self._inflight: dict[str, _BranchFlight] = {}

    @property
    def coordinator(self) -> NativeBranchCoordinator:
        if self._coordinator is None:
            self._coordinator = NativeBranchCoordinator(
                self.engine,
                self.config.bounds,
                max_branches_per_source=self.config.max_branches_per_source,
                allow_branch_of_lineage=False,
            )
        return self._coordinator

    async def open(self) -> None:
        self.path = self.services.engine_database_path("langgraph")
        if self.path == ":memory:" or (not is_postgresql_datasource(self.path) and not self.path):
            raise ConfigurationError("native state branch requires durable native storage")
        self.opened = True

    async def close(self) -> None:
        tasks = tuple(flight.task for flight in self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        self.opened = False
        if self.services.native_branch_controller is self:
            self.services.native_branch_controller = None

    def verify(self, configuration: NativeStateBranchConfig | None) -> None:
        if (
            not self.opened
            or configuration != self.config
            or self.services.config.native_state_branch != self.config
            or self.services.native_branch_controller is not self
            or self.path != self.services.engine_database_path("langgraph", create_namespace=False)
            or not self.services.config.execution_authorization.enabled
            or not self.services.config.server.api_security_profile
            or self.services.config.native_state is None
            or self.services.config.native_state_updates is None
            or self.engine.state_controller is None
        ):
            raise ConfigurationError("native state branch installation or policy changed")
        self.engine.state_controller.verify(self.services.config.native_state_updates)

    async def admit(
        self,
        handle: ExecutionHandle,
        authority: ExecutionAuthority,
        *,
        request_id: str,
        reference: str,
        task_reference: str,
        values: dict[str, Any],
        acknowledge_reexecution: bool,
    ) -> dict[str, Any]:
        self.verify(self.services.config.native_state_branch)
        if self.coordinator.reservations.is_replay_target(handle.invocation_id):
            raise InvocationStateError("a lineage target cannot itself be branched")
        plan = await self.services.recovery_definitions.resolve_inspection(handle)
        policy, updates = plan.native_state_branch, plan.native_state_updates
        if policy is None or updates is None:
            raise UnsupportedWorkflowFeature("workflow does not declare native state branch")
        if policy.authorization_policy_ref != self.services.config.execution_authorization.id:
            raise UnsupportedWorkflowFeature("native state branch authorization is unavailable")
        if acknowledge_reexecution != (policy.effect_policy == "reexecute"):
            raise InvocationStateError("state branch effect acknowledgement does not match policy")
        encoded = updates.validate(task_reference, values, policy.authorization_policy_ref)
        flight = self._inflight.get(request_id)
        if flight is not None and (
            flight.source_id != handle.invocation_id
            or flight.reference != reference
            or flight.task_reference != task_reference
            or flight.values_json != encoded
            or flight.acknowledge_reexecution != acknowledge_reexecution
        ):
            raise lineage_error("reservation_request_invalid")
        if flight is None:
            if (
                sum(not item.task.done() for item in self._inflight.values())
                >= self.config.max_active_branches
            ):
                raise NativeStateCapacity("native state branch capacity is exhausted")
            bound: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            target: dict[str, str] = {}

            def phase(name: str, target_id: str) -> None:
                target["id"] = target_id
                if name == "bound" and not bound.done():
                    bound.set_result(target_id)

            task = asyncio.create_task(
                self.coordinator.run_branch(
                    handle,
                    authority,
                    request_id=request_id,
                    source_reference=reference,
                    task_reference=task_reference,
                    values=json.loads(encoded),
                    acknowledge_reexecution=acknowledge_reexecution,
                    _phase=phase,
                )
            )
            flight = _BranchFlight(
                task,
                bound,
                target,
                handle.invocation_id,
                reference,
                task_reference,
                encoded,
                acknowledge_reexecution,
            )
            self._inflight[request_id] = flight
            task.add_done_callback(_consume)
            task.add_done_callback(lambda _: self._inflight.pop(request_id, None))
        await asyncio.wait({flight.task, flight.bound}, return_when=asyncio.FIRST_COMPLETED)
        if flight.bound.done():
            target_id = flight.bound.result()
        else:
            error = flight.task.exception()
            if error is not None:
                raise error
            if "id" not in flight.target:
                raise InvocationStateError("state branch did not produce an invocation")
            target_id = flight.target["id"]
        return {
            "invocation_id": target_id,
            "source_invocation_id": handle.invocation_id,
            "effect_policy": policy.effect_policy,
            "state": "accepted",
        }
