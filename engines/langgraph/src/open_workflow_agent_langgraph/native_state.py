"""Bounded native root-checkpoint views with opaque, invocation-scoped references."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any

from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.errors import ConfigurationError, UnsupportedWorkflowFeature
from open_workflow_agent.native_state import (
    CAPABILITY,
    NativeStateCapacity,
    NativeStateQuery,
    NativeStateResult,
    NativeStateUnavailable,
)
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import WorkflowPlan

from .graph import LangGraphScopeExecutor, _json_identity
from .native_state_storage import checkpoint_index, guard_checkpoint, readonly_saver


def state_reference(invocation_id: str, fingerprint: str, checkpoint_id: str) -> str:
    value = json.dumps(["owa/native-state-reference/v1", invocation_id, fingerprint, checkpoint_id])
    return hashlib.sha256(value.encode()).hexdigest()


class LangGraphStateReader:
    def __init__(self, services: RuntimeServices) -> None:
        if services.config.native_state is None:
            raise ConfigurationError("native state policy is absent")
        self.services = services
        self.configuration = NativeStateConfig.model_validate(
            services.config.native_state.model_dump(mode="json")
        )
        self._policy = self.configuration.model_dump(mode="json")
        self.path = services.engine_database_path("langgraph", create_namespace=False)
        self._slots = asyncio.Semaphore(self.configuration.max_concurrency)

    def verify(self, configuration: NativeStateConfig | None) -> None:
        if (
            configuration is None
            or configuration.model_dump(mode="json") != self._policy
            or self.configuration.model_dump(mode="json") != self._policy
            or self.path != self.services.engine_database_path("langgraph", create_namespace=False)
        ):
            raise ConfigurationError("native state policy or storage binding changed")

    async def read(
        self, handle: ExecutionHandle, plan: WorkflowPlan, query: NativeStateQuery
    ) -> NativeStateResult:
        self.verify(self.services.config.native_state)
        if handle.engine != "langgraph" or handle.workflow_fingerprint != plan.fingerprint:
            raise NativeStateUnavailable("native state workflow binding is inconsistent")
        if not any(
            item.required and item.capability_id == CAPABILITY for item in plan.native_extensions
        ):
            raise UnsupportedWorkflowFeature(
                "workflow does not require the native state read profile"
            )
        if query.limit > self.configuration.max_page_size:
            raise UnsupportedWorkflowFeature("native state page exceeds its configured bound")
        if self._slots.locked():
            raise NativeStateCapacity("native state reader capacity is exhausted")
        try:
            async with self._slots:
                task = asyncio.create_task(self._read(handle, plan, query))
                try:
                    async with asyncio.timeout(self.configuration.timeout_seconds):
                        return await asyncio.shield(task)
                except BaseException:
                    task.cancel()
                    while not task.done():
                        try:
                            await asyncio.shield(task)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not task.cancelled():
                        task.exception()
                    raise
        except (NativeStateUnavailable, NativeStateCapacity, UnsupportedWorkflowFeature):
            raise
        except TimeoutError:
            raise NativeStateUnavailable("native state read timed out") from None
        except Exception:
            raise NativeStateUnavailable(
                "native state is absent, inconsistent or exceeds its bounds"
            ) from None

    async def _read(
        self, handle: ExecutionHandle, plan: WorkflowPlan, query: NativeStateQuery
    ) -> NativeStateResult:
        bounds = self.configuration
        async with readonly_saver(self.path, bounds) as (saver, postgres):
            driver = LangGraphScopeExecutor(plan, saver, handle.invocation_id)
            root_config = driver.checkpoint_config("/do", 1)
            thread = root_config["configurable"]["thread_id"]
            index, truncated = await checkpoint_index(saver, thread, bounds, postgres)
            references = [
                state_reference(handle.invocation_id, plan.fingerprint, key) for key, _ in index
            ]
            selector = query.reference if query.kind == "state" else query.before
            if selector is not None and selector not in references:
                raise NativeStateUnavailable(
                    "native state reference is outside the available history window"
                )
            offset = references.index(selector) if selector is not None else 0
            if query.kind == "history" and selector is not None:
                offset += 1
            selected = index[offset : offset + query.limit]
            if query.kind == "state" and not selected:
                raise NativeStateUnavailable("native state has not been retained")
            budget = bounds.max_native_bytes
            # Size/array guards all run before any selected SDK value is decoded.
            for key, _ in selected:
                budget -= await guard_checkpoint(saver, thread, key, budget, postgres)
            expected_input = self.services.invocations.read_input(handle)
            checkpoints = []
            for key, parent in selected:
                config = {
                    **root_config,
                    "configurable": {
                        **root_config["configurable"],
                        "checkpoint_id": key,
                    },
                }
                checkpoint = await saver.aget_tuple(config)
                if checkpoint is None:
                    raise ValueError("checkpoint disappeared from its read snapshot")
                checkpoints.append((checkpoint, key, parent))
            # Common receipt/archive verification is synchronous inside one
            # fresh physical-profile phase, after native I/O has completed.
            async with self.services.recovery_definitions.inspection_profile(handle) as verified:
                if verified.fingerprint != plan.fingerprint:
                    raise ValueError("native state definition changed during its read")
                items = [
                    self._project(
                        handle, plan, saver, checkpoint, thread, key, parent, expected_input
                    )
                    for checkpoint, key, parent in checkpoints
                ]
            value = {
                "profile": CAPABILITY,
                "invocation_id": handle.invocation_id,
                "workflow_fingerprint": plan.fingerprint,
                "scope": "/do",
                "verification": "snapshot_and_immutable_receipt_bindings",
                "continuation": "not_inspected",
                "items": items,
                "next_before": references[offset + len(items) - 1]
                if query.kind == "history" and items and offset + len(items) < len(index)
                else None,
                "history_window_limited": truncated,
            }
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if len(encoded.encode()) > bounds.max_response_bytes:
                raise NativeStateUnavailable("native state projection exceeds its response bound")
            return NativeStateResult(encoded)

    def _project(
        self,
        handle: ExecutionHandle,
        plan: WorkflowPlan,
        saver: Any,
        checkpoint: Any,
        thread: str,
        key: str,
        parent: str | None,
        expected_input: Any,
    ) -> dict[str, Any]:
        native_config = checkpoint.config["configurable"]
        if (
            native_config.get("thread_id") != thread
            or native_config.get("checkpoint_ns") != ""
            or native_config.get("checkpoint_id") != key
            or checkpoint.checkpoint.get("id") != key
            or type(checkpoint.checkpoint.get("v")) is not int
            or checkpoint.checkpoint["v"] != 4
            or not isinstance(checkpoint.metadata, dict)
            or checkpoint.metadata.get("source") not in {"input", "loop", "update", "fork"}
            or type(checkpoint.metadata.get("step")) is not int
            or (checkpoint.parent_config or {}).get("configurable", {}).get("checkpoint_id")
            != parent
        ):
            raise ValueError("native checkpoint identity is inconsistent")
        timestamp = checkpoint.checkpoint.get("ts")
        if not isinstance(timestamp, str) or len(timestamp) > 128:
            raise ValueError("native checkpoint timestamp is invalid")
        if datetime.fromisoformat(timestamp).tzinfo is None:
            raise ValueError("native checkpoint timestamp is not absolute")
        driver = LangGraphScopeExecutor(
            plan,
            saver,
            handle.invocation_id,
            effect_journal=self.services.effect_journal,
        )
        driver._expected_input_json = _json_identity(expected_input)
        native = checkpoint.checkpoint["channel_values"]
        phase = "checkpoint"
        if set(native) == {"__start__"}:
            driver.validate_historical_initial_projection(checkpoint, expected_input)
            native = native["__start__"]
            phase = "initial"
        decoded, _ = driver._validate_native(native, "/do", 1)
        return {
            "reference": state_reference(handle.invocation_id, plan.fingerprint, key),
            "parent_reference": state_reference(handle.invocation_id, plan.fingerprint, parent)
            if parent is not None
            else None,
            "created_at": timestamp,
            "phase": phase,
            "root_task_visits": {
                task.reference: decoded["task_visits"][task.execution_reference or task.reference]
                for task in driver.scopes["/do"].tasks
                if (task.execution_reference or task.reference) in decoded["task_visits"]
            },
            "data": decoded["data"],
            "channels": native.get("native_channels", {}),
        }
