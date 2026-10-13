"""Workflow-facing executor extension that routes run tasks to SandboxManager."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import asdict, replace
from typing import Any, cast

from ..errors import SandboxPolicyError, WorkflowExecutionError, WorkflowSemanticError
from ..expressions import expression_string
from ..persistence import ExecutionHandle
from ..workflow import _ACTIVE_TIMEOUTS, _RECONCILE_EXPIRED_RETRY, ExecutionState, WorkflowExecutor
from .contract import SandboxExecutionRequest, SandboxExecutionResult, SandboxSecretReference
from .jobs import SandboxJobStore, _request, job_id, policy_identity


class SandboxWorkflowExecutor(WorkflowExecutor):
    """Common executor extension that routes executable run tasks to SandboxManager."""

    async def _run_subworkflow(self, definition: Any, state: ExecutionState) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("run configuration must be an object")
        executable = bool({"script", "shell", "container"}.intersection(definition))
        mode = definition.get("return", "stdout")
        if executable and (
            not isinstance(mode, str) or mode not in {"stdout", "stderr", "code", "all", "none"}
        ):
            raise WorkflowSemanticError("invalid executable run return mode")
        background = executable and definition.get("await", True) is False
        if "script" in definition:
            result = await self._run_script(definition["script"], state, background=background)
        elif "shell" in definition:
            result = await self._run_shell(definition["shell"], state, background=background)
        elif "container" in definition:
            result = await self._run_container(
                definition["container"], state, background=background
            )
        else:
            return await super()._run_subworkflow(definition, state)
        if background:
            return result
        # Keep the complete bounded result in the common receipt. Projection
        # happens only after that known outcome has been retained or verified.
        if mode == "all":
            return result
        if mode == "none":
            return None
        return result[mode]

    async def _run_script(
        self, definition: Any, state: ExecutionState, *, background: bool = False
    ) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("run.script must be an object")
        request = SandboxExecutionRequest(
            execution_id=self._execution_id(state, "script"),
            kind="script",
            arguments=self._arguments(definition, state),
            stdin=self._stdin(definition, state),
            environment=self._environment(definition, state),
            script_language=str(definition.get("language", "")),
            script_code=cast(str | None, definition.get("code")),
            invocation_id=cast(str | None, state.metadata.get("invocation_id")),
            task_reference=cast(str | None, state.variables.get("_task_reference")),
        )
        original_reference = state.metadata.get("_source_references", {}).get(
            request.task_reference, request.task_reference
        )
        captured = state.metadata.get("_script_resources", {}).get(original_reference)
        handle = state.metadata.get("_invocation_handle")
        if self.services is not None and isinstance(handle, ExecutionHandle):
            original_plan = self.services.recovery_definitions.verify_admission(handle)
            original_task = next(
                (
                    task
                    for scope in original_plan.scopes
                    for task in scope.tasks
                    if (task.execution_reference or task.reference) == request.task_reference
                ),
                None,
            )
            if original_task is None or original_task.reference != original_reference:
                raise SandboxPolicyError("executable task differs from original admission")
            required = next(
                (
                    item
                    for item in original_plan.script_resources
                    if item.reference == original_reference
                ),
                None,
            )
            if (required is None) != (captured is None) or (
                required is not None and required.as_value() != captured
            ):
                raise SandboxPolicyError(
                    "executable source capture differs from original admission"
                )
        if captured is not None:
            from ..executable_sources import ExecutableScriptBinding

            binding = ExecutableScriptBinding.from_value(captured)
            lease = self._execution_lease(state)
            if self.services is None or not isinstance(handle, ExecutionHandle) or lease is None:
                raise SandboxPolicyError("executable source admission is unavailable")
            identity = self._effect_identity(state, "run:script")
            if background:
                request = replace(request, execution_id=job_id(identity))
                payload = {"request": _request(request, identity), "input": state.data}
            else:
                payload = json.loads(json.dumps(asdict(request), allow_nan=False))
            deadlines = [
                scope.absolute_due
                for scope in _ACTIVE_TIMEOUTS.get()
                if scope.absolute_due is not None
            ]
            decision = self.services.executable_source_admissions.prepare(
                identity,
                payload,
                binding.source,
                handle,
                execution_lease=lease,
                deadline_at=min(deadlines) if deadlines else None,
                allow_new=not _RECONCILE_EXPIRED_RETRY.get(),
            )
            request = replace(request, script_code=decision.source.verified_bytes().decode("utf-8"))
            await self._notify_receipt(state, decision.identity, decision.receipt)
        return await (
            self._execute_background(request, state)
            if background
            else self._execute_sandbox(request, state)
        )

    async def _run_shell(
        self, definition: Any, state: ExecutionState, *, background: bool = False
    ) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("run.shell must be an object")
        request = SandboxExecutionRequest(
            execution_id=self._execution_id(state, "shell"),
            kind="shell",
            command=str(definition.get("command", "")),
            arguments=self._arguments(definition, state),
            stdin=self._stdin(definition, state),
            environment=self._environment(definition, state),
            invocation_id=cast(str | None, state.metadata.get("invocation_id")),
            task_reference=cast(str | None, state.variables.get("_task_reference")),
        )
        return await (
            self._execute_background(request, state)
            if background
            else self._execute_sandbox(request, state)
        )

    async def _run_container(
        self, definition: Any, state: ExecutionState, *, background: bool = False
    ) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("run.container must be an object")
        request = SandboxExecutionRequest(
            execution_id=self._execution_id(state, "container"),
            kind="container",
            image=str(definition.get("image", "")),
            command=(str(definition["command"]) if definition.get("command") else None),
            arguments=self._arguments(definition, state),
            stdin=self._stdin(definition, state),
            environment=self._environment(definition, state),
            invocation_id=cast(str | None, state.metadata.get("invocation_id")),
            task_reference=cast(str | None, state.variables.get("_task_reference")),
        )
        return await (
            self._execute_background(request, state)
            if background
            else self._execute_sandbox(request, state)
        )

    async def _execute_background(
        self, request: SandboxExecutionRequest, state: ExecutionState
    ) -> Any:
        store = getattr(self.services, "sandbox_jobs", None)
        handle = state.metadata.get("_invocation_handle")
        lease = self._execution_lease(state)
        if (
            not isinstance(store, SandboxJobStore)
            or not isinstance(handle, ExecutionHandle)
            or lease is None
        ):
            raise SandboxPolicyError("background execution admission is unavailable")
        identity = self._effect_identity(state, "run:" + request.kind)
        request = replace(request, execution_id=job_id(identity))
        self.services.sandbox.validate_job_request(request)
        prior = store.get(request.execution_id)
        if prior is None and _RECONCILE_EXPIRED_RETRY.get():
            raise WorkflowExecutionError("retry budget expired before a new sandbox job")
        if prior is not None:
            deadline = json.loads(prior.admission_json)["deadline_at"]
        else:
            deadlines = [
                scope.absolute_due
                for scope in _ACTIVE_TIMEOUTS.get()
                if scope.absolute_due is not None
            ]
            deadline = min(
                [self._absolute_clock() + store.profile_provider().timeout_seconds, *deadlines]
            )
        job, receipt = store.admit_action(
            identity,
            handle,
            request,
            state.data,
            deadline_at=deadline,
            execution_lease=lease,
        )
        await self._notify_receipt(state, policy_identity(identity), job.receipt)
        await self._notify_receipt(state, identity, receipt)
        # This is acceptance of an independent job, not a process exit.
        return receipt.output

    async def _execute_sandbox(
        self, request: SandboxExecutionRequest, state: ExecutionState
    ) -> dict[str, Any]:
        if self.services is None or not hasattr(self.services, "sandbox"):
            raise SandboxPolicyError("sandbox service is unavailable")
        reference = str(state.variables.get("_task_reference", "unknown-task"))
        name = state.variables.get("_task_name", "run")
        event = {
            **self._task_event(reference, name, state),
            "execution_id": request.execution_id,
        }

        async def dispatch() -> dict[str, Any]:
            self._emit("SandboxExecutionStarted", {**event, "status": "running"})
            self._emit(
                "TaskProgress",
                {**event, "status": "running", "progress": {"phase": "sandbox_start"}},
            )
            result = await self._await_with_cancellation(
                self.services.sandbox.execute(request), state
            )
            return asdict(cast(SandboxExecutionResult, result))

        try:
            # Secret references stay references. Only the request digest is
            # retained, never the request or resolved deployment environment.
            prepared = json.loads(json.dumps(asdict(request), allow_nan=False))
            operation = "run:" + request.kind
            result = await self._run_effect(
                operation, prepared, state, dispatch, operation=operation
            )
        except asyncio.CancelledError:
            self._emit(
                "SandboxExecutionCancelled",
                {
                    **event,
                    "status": "cancelled",
                    "error": {"code": "invocation_cancelled"},
                },
            )
            raise
        except Exception as exc:
            code = getattr(exc, "code", "sandbox_error")
            self._emit(
                "SandboxExecutionFailed",
                {
                    **event,
                    "status": "faulted",
                    "error": {"code": str(code)},
                },
            )
            raise
        typed_result = SandboxExecutionResult(**result)
        self._emit(
            "SandboxExecutionCompleted",
            {
                **event,
                "status": "completed",
                "duration": typed_result.duration,
            },
        )
        self._emit(
            "TaskProgress",
            {
                **event,
                "status": "running",
                "duration": typed_result.duration,
                "progress": {"phase": "sandbox_finished"},
            },
        )
        return typed_result.as_output()

    def _arguments(self, definition: Mapping[str, Any], state: ExecutionState) -> tuple[str, ...]:
        values = self._expressions(state).evaluate(
            definition.get("arguments", []), state.data, variables=state.variables
        )
        if not isinstance(values, list):
            raise WorkflowSemanticError("sandbox arguments must evaluate to an array")
        return tuple(expression_string(value) for value in values)

    def _stdin(self, definition: Mapping[str, Any], state: ExecutionState) -> str | None:
        value = definition.get("stdin")
        if value is None:
            return None
        evaluated = self._expressions(state).evaluate(value, state.data, variables=state.variables)
        return expression_string(evaluated)

    def _environment(
        self, definition: Mapping[str, Any], state: ExecutionState
    ) -> tuple[tuple[str, str | SandboxSecretReference], ...]:
        value = definition.get("environment", {})
        if not isinstance(value, Mapping):
            raise WorkflowSemanticError("sandbox environment must be an object")
        result: list[tuple[str, str | SandboxSecretReference]] = []
        for name, raw in value.items():
            if isinstance(raw, Mapping) and set(raw) == {"fromEnv"}:
                reference = raw.get("fromEnv")
                if not isinstance(reference, str):
                    raise WorkflowSemanticError("sandbox fromEnv reference must be a string")
                result.append((str(name), SandboxSecretReference(reference)))
                continue
            evaluated = self._expressions(state).evaluate(
                raw, state.data, variables=state.variables
            )
            result.append((str(name), expression_string(evaluated)))
        return tuple(result)

    def _execution_id(self, state: ExecutionState, kind: str) -> str:
        invocation = state.metadata.get("invocation_id", "unknown-invocation")
        reference = state.variables.get("_task_reference", "unknown-task")
        occurrence = self._effect_identity(state, "run:" + kind).key[:12]
        return f"{invocation}:{reference}:{occurrence}"
