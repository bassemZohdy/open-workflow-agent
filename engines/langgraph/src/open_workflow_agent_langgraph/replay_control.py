"""Public replay admission over the private root replay coordinator.

The coordinator stages are durable and idempotent. This controller admits and binds a
replay synchronously, then lets the new invocation continue in a tracked background task.
It exposes no graph, thread or checkpoint identifiers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from open_workflow_agent.config import NativeStateReplayConfig
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

from .native_replay_coordinator import NativeReplayCoordinator

if TYPE_CHECKING:
    from open_workflow_agent.services import RuntimeServices

    from . import LangGraphWorkflowEngine


@dataclass(slots=True)
class _Flight:
    """One admitted request; followers with the same key share its outcome."""

    task: asyncio.Task[Any]
    bound: asyncio.Future[str]
    target: dict[str, str]
    source_id: str
    reference: str
    acknowledge_reexecution: bool


class LangGraphReplayController:
    def __init__(self, services: RuntimeServices, engine: LangGraphWorkflowEngine) -> None:
        configuration = services.config.native_state_replay
        if configuration is None:
            raise ConfigurationError("native state replay is not configured")
        self.services = services
        self.engine = engine
        self.config: NativeStateReplayConfig = configuration
        self._coordinator: NativeReplayCoordinator | None = None
        self.path = ""
        self.opened = False
        self._inflight: dict[str, _Flight] = {}

    @property
    def coordinator(self) -> NativeReplayCoordinator:
        # The engine assigns its services after controllers open, so build this on first use.
        if self._coordinator is None:
            self._coordinator = NativeReplayCoordinator(
                self.engine,
                self.config.bounds,
                max_replays_per_source=self.config.max_replays_per_source,
                allow_replay_of_replay=False,
            )
        return self._coordinator

    async def open(self) -> None:
        self.path = self.services.engine_database_path("langgraph")
        postgres = is_postgresql_datasource(self.path)
        if self.path == ":memory:" or (not postgres and not self.path):
            raise ConfigurationError("native state replay requires durable native storage")
        self.opened = True

    async def close(self) -> None:
        tasks = tuple(flight.task for flight in self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        if self.services.native_replay_controller is self:
            self.services.native_replay_controller = None

    def verify(self, configuration: NativeStateReplayConfig | None) -> None:
        if (
            not self.opened
            or configuration != self.config
            or self.services.config.native_state_replay != self.config
            or self.services.native_replay_controller is not self
            or self.path != self.services.engine_database_path("langgraph", create_namespace=False)
            or not self.services.config.execution_authorization.enabled
            or not self.services.config.server.api_security_profile
            or self.services.config.native_state is None
        ):
            raise ConfigurationError("native state replay installation or policy changed")

    def _active(self) -> int:
        return sum(1 for flight in self._inflight.values() if not flight.task.done())

    async def admit(
        self,
        handle: ExecutionHandle,
        authority: ExecutionAuthority,
        *,
        request_id: str,
        reference: str,
        acknowledge_reexecution: bool,
    ) -> dict[str, Any]:
        self.verify(self.services.config.native_state_replay)
        if self.coordinator.reservations.is_replay_target(handle.invocation_id):
            raise InvocationStateError(
                "a replay target cannot itself be replayed",
                details={"stage": "state_lineage", "reason": "replay_depth"},
            )
        plan = await self.services.recovery_definitions.resolve_inspection(handle)
        policy = plan.native_state_replay
        if policy is None:
            raise UnsupportedWorkflowFeature("workflow does not declare native state replay")
        if policy.authorization_policy_ref != self.services.config.execution_authorization.id:
            raise UnsupportedWorkflowFeature("native state replay authorization is unavailable")
        if acknowledge_reexecution != (policy.effect_policy == "reexecute"):
            raise InvocationStateError(
                "state replay requires an effect acknowledgement matching the declared policy"
            )
        response: dict[str, Any] = {
            "source_invocation_id": handle.invocation_id,
            "effect_policy": policy.effect_policy,
            "state": "accepted",
        }
        flight = self._inflight.get(request_id)
        if flight is not None and (
            flight.source_id != handle.invocation_id
            or flight.reference != reference
            or flight.acknowledge_reexecution != acknowledge_reexecution
        ):
            raise lineage_error("reservation_request_invalid")
        if flight is None:
            if self._active() >= self.config.max_active_replays:
                raise NativeStateCapacity("native state replay capacity is exhausted")
            flight = self._start(handle, authority, request_id, reference, acknowledge_reexecution)
        await asyncio.wait({flight.task, flight.bound}, return_when=asyncio.FIRST_COMPLETED)
        if flight.bound.done():
            return {**response, "invocation_id": flight.bound.result()}
        # The coordinator finished before binding: a completed repeat or an admission failure.
        error = flight.task.exception()
        if error is not None:
            raise error
        if "id" not in flight.target:
            raise InvocationStateError("state replay did not produce an invocation")
        return {**response, "invocation_id": flight.target["id"]}

    def _start(
        self,
        handle: ExecutionHandle,
        authority: ExecutionAuthority,
        request_id: str,
        reference: str,
        acknowledge_reexecution: bool,
    ) -> _Flight:
        bound: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        target: dict[str, str] = {}

        def phase(name: str, target_id: str) -> None:
            target["id"] = target_id
            if name == "bound" and not bound.done():
                bound.set_result(target_id)

        task = asyncio.ensure_future(
            self.coordinator.run(
                handle,
                authority,
                request_id=request_id,
                source_reference=reference,
                acknowledge_reexecution=acknowledge_reexecution,
                _phase=phase,
            )
        )
        flight = _Flight(
            task, bound, target, handle.invocation_id, reference, acknowledge_reexecution
        )
        self._inflight[request_id] = flight
        task.add_done_callback(_consume)
        task.add_done_callback(lambda _: self._forget(request_id))
        return flight

    def _forget(self, request_id: str) -> None:
        self._inflight.pop(request_id, None)


def _consume(task: asyncio.Task[Any]) -> None:
    """Retrieve a background continuation's outcome so it is never reported as unhandled."""
    if not task.cancelled():
        task.exception()
