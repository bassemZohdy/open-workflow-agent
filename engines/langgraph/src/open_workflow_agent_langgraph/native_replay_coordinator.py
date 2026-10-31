"""Private root-replay coordinator with durable, restartable stages.

This is not a public admission boundary. Callers must supply trusted execution
authority and use only the explicitly supported root checkpoint profile.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.execution_authority import ExecutionAuthority, authority_scope
from open_workflow_agent.lineage_registry import LineageRegistry
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.state_lineage import PREFIX, lineage_error
from open_workflow_agent.storage import is_postgresql_datasource
from open_workflow_agent.workflow import ExecutionState

from . import native
from .graph import LangGraphScopeExecutor, _node_name
from .native_lineage_fork import apply_native_lineage_fork, prepare_lineage_storage
from .native_lineage_inspection import inspect_native_lineage
from .native_lineage_preflight import prepare_native_lineage
from .native_lineage_reservation import NativeReplayReservations
from .native_lineage_transition import stage_root_replay

if TYPE_CHECKING:
    from . import LangGraphWorkflowEngine


class NativeReplayCoordinator:
    """Reconcile a single root replay; no public capability is advertised."""

    def __init__(self, engine: LangGraphWorkflowEngine, bounds: NativeStateConfig) -> None:
        self.engine = engine
        self.services = engine.services
        self.bounds = NativeStateConfig.model_validate(bounds.model_dump())
        self.reservations = NativeReplayReservations(self.services)

    async def run(
        self,
        source: ExecutionHandle,
        authority: ExecutionAuthority,
        *,
        request_id: str,
        source_reference: str,
        acknowledge_reexecution: bool = False,
        _phase: Callable[[str, str], None] | None = None,
    ) -> Any:
        if type(request_id) is not str or not re.fullmatch(r"[a-f0-9]{64}", request_id):
            raise lineage_error("request_invalid")
        notify = _phase or (lambda _name, _target: None)
        key = EffectIdentity(
            source.invocation_id,
            source.workflow_fingerprint,
            "/do",
            (("/do", 1),),
            PREFIX + request_id,
        ).key
        row = self.reservations._row(key)
        if row is None:
            prepared = await prepare_native_lineage(
                self.services,
                source,
                authority,
                kind="replay",
                target_invocation_id=str(uuid4()),
                request_id=request_id,
                source_reference=source_reference,
                acknowledge_reexecution=acknowledge_reexecution,
                bounds=self.bounds,
            )
            plan = await self.services.recovery_definitions.resolve_inspection(source)
            target = self.reservations.reserve(source, authority, plan, prepared)
        else:
            request, target = self.reservations.recover(source, authority, request_id)
            if (
                request.source_reference != source_reference
                or request.acknowledge_reexecution != acknowledge_reexecution
            ):
                raise lineage_error("reservation_request_invalid")
            prepared = await prepare_native_lineage(
                self.services,
                source,
                authority,
                kind="replay",
                target_invocation_id=target.invocation_id,
                request_id=request_id,
                source_reference=source_reference,
                acknowledge_reexecution=acknowledge_reexecution,
                bounds=self.bounds,
                _reserved_request=request,
            )
            plan = await self.services.recovery_definitions.resolve_inspection(source)
        notify("reserved", target.invocation_id)
        original = self.services.execution_authorizations.verify_invocation(source)
        if original is None:
            raise lineage_error("original_authority_required")
        path = self.services.engine_database_path("langgraph", create_namespace=False)
        postgres = is_postgresql_datasource(path)
        saver_type = getattr(native, "AsyncPostgresSaver" if postgres else "AsyncSqliteSaver", None)
        if saver_type is None:
            raise lineage_error("native_storage_unavailable")
        await self.engine.native.prepare_storage()
        async with saver_type.from_conn_string(path) as saver:
            await native._prepare_saver(saver, path)
            await prepare_lineage_storage(saver, postgresql=postgres)
        if target.status != "running":
            registry = self.engine._resume_lineage(target)
            if registry is None:
                raise lineage_error("lineage_binding_missing")
            return self.engine._stored_result(target)
        with authority_scope(original):
            async with self.engine._own_execution_scoped(target):
                archive = self.services.recovery_definitions
                auth = self.services.execution_authorizations
                if not archive.has_binding(target):
                    candidate = auth.reserved_invocation(target)
                    if candidate is None:
                        auth.reserve_invocation(
                            target, original, input_data=json.loads(prepared.input_json)
                        )
                    elif candidate != original:
                        raise lineage_error("original_authority_required")
                    async with archive.owned_profile("langgraph"):
                        archive.admit(
                            plan,
                            target,
                            json.loads(prepared.input_json),
                            execution_lease=target._execution_lease,
                        )
                elif auth.verify_invocation(target) != original:
                    raise lineage_error("original_authority_required")
                if (await archive.resolve_inspection(target)).fingerprint != plan.fingerprint:
                    raise lineage_error("source_definition_changed")
                notify("admitted", target.invocation_id)
                proposal, dependencies = stage_root_replay(self.services, plan, prepared, target)
                snapshot = json.loads(json.loads(proposal.packets[0].native_json)["snapshot"])
                first = plan.scopes[0].tasks[0]
                if snapshot["task_visits"] != {first.execution_reference or first.reference: 1}:
                    raise lineage_error("replay_profile_unsupported")

                async def write(saver: Any, configs: Any) -> tuple[Any, ...]:
                    driver = LangGraphScopeExecutor(plan, saver, target.invocation_id)
                    graph = driver.compile_scope(
                        plan.scopes[0], self.engine.executor, ExecutionState(data=None)
                    )
                    result = await graph.aupdate_state(
                        configs[0],
                        json.loads(proposal.packets[0].native_json),
                        as_node=_node_name(first),
                    )
                    return (result,)

                if target._execution_lease is None:
                    raise lineage_error("effect_ownership_lost")
                await apply_native_lineage_fork(
                    path,
                    self.services.execution_ownership.native_fence(target._execution_lease),
                    target,
                    plan,
                    proposal,
                    write,
                    bounds=self.bounds,
                )
                notify("forked", target.invocation_id)
                digest = inspect_native_lineage(path, prepared.request, self.bounds)
                if digest is None:
                    raise lineage_error("native_proof_unavailable")
                registry = LineageRegistry(
                    self.services.effect_journal,
                    self.services.invocations,
                    auth,
                    lambda request, value: (
                        inspect_native_lineage(path, request, self.bounds) == value
                    ),
                )
                registry.bind(prepared.request, target, authority, digest, dependencies)
                notify("bound", target.invocation_id)
                await self.engine.native.admit_resume(
                    plan,
                    target.invocation_id,
                    json.loads(prepared.input_json),
                    effect_journal=self.services.effect_journal,
                    lineage_registry=registry,
                    lineage_target=target,
                )
                return await self.engine._resume_owned(target, None, plan)
