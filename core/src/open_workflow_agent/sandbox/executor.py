"""Workflow-facing executor extension that routes run tasks to SandboxManager."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, cast
from uuid import uuid4

from ..errors import SandboxPolicyError, WorkflowSemanticError
from ..workflow import ExecutionState, WorkflowExecutor
from .contract import SandboxExecutionRequest, SandboxExecutionResult, SandboxSecretReference


class SandboxWorkflowExecutor(WorkflowExecutor):
    """Common executor extension that routes executable run tasks to SandboxManager."""

    async def _run_subworkflow(self, definition: Any, state: ExecutionState) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("run configuration must be an object")
        if "script" in definition:
            return await self._run_script(definition["script"], state)
        if "shell" in definition:
            return await self._run_shell(definition["shell"], state)
        if "container" in definition:
            return await self._run_container(definition["container"], state)
        return await super()._run_subworkflow(definition, state)

    async def _run_script(self, definition: Any, state: ExecutionState) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("run.script must be an object")
        request = SandboxExecutionRequest(
            execution_id=self._execution_id(state),
            kind="script",
            arguments=tuple(str(value) for value in definition.get("arguments", [])),
            stdin=self._stdin(definition, state),
            environment=self._environment(definition, state),
            script_language=str(definition.get("language", "")),
            script_code=cast(str | None, definition.get("code")),
            invocation_id=cast(str | None, state.context.get("invocation_id")),
            task_reference=cast(str | None, state.variables.get("_task_reference")),
        )
        return await self._execute_sandbox(request, state)

    async def _run_shell(self, definition: Any, state: ExecutionState) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("run.shell must be an object")
        request = SandboxExecutionRequest(
            execution_id=self._execution_id(state),
            kind="shell",
            command=str(definition.get("command", "")),
            arguments=tuple(str(value) for value in definition.get("arguments", [])),
            stdin=self._stdin(definition, state),
            environment=self._environment(definition, state),
            invocation_id=cast(str | None, state.context.get("invocation_id")),
            task_reference=cast(str | None, state.variables.get("_task_reference")),
        )
        return await self._execute_sandbox(request, state)

    async def _run_container(self, definition: Any, state: ExecutionState) -> Any:
        if not isinstance(definition, Mapping):
            raise WorkflowSemanticError("run.container must be an object")
        request = SandboxExecutionRequest(
            execution_id=self._execution_id(state),
            kind="container",
            image=str(definition.get("image", "")),
            command=(str(definition["command"]) if definition.get("command") else None),
            arguments=tuple(str(value) for value in definition.get("arguments", [])),
            stdin=self._stdin(definition, state),
            environment=self._environment(definition, state),
            invocation_id=cast(str | None, state.context.get("invocation_id")),
            task_reference=cast(str | None, state.variables.get("_task_reference")),
        )
        return await self._execute_sandbox(request, state)

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
        self._emit("SandboxExecutionStarted", {**event, "status": "running"})
        self._emit(
            "TaskProgress",
            {
                **event,
                "status": "running",
                "progress": {"phase": "sandbox_start"},
            },
        )
        try:
            result = await self._await_with_cancellation(
                self.services.sandbox.execute(request), state
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
        typed_result = cast(SandboxExecutionResult, result)
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

    def _stdin(self, definition: Mapping[str, Any], state: ExecutionState) -> str | None:
        value = definition.get("stdin")
        if value is None:
            return None
        evaluated = self.expressions.evaluate(value, state.data, variables=state.variables)
        return evaluated if isinstance(evaluated, str) else str(evaluated)

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
            evaluated = self.expressions.evaluate(raw, state.data, variables=state.variables)
            result.append((str(name), evaluated if isinstance(evaluated, str) else str(evaluated)))
        return tuple(result)

    @staticmethod
    def _execution_id(state: ExecutionState) -> str:
        invocation = state.context.get("invocation_id", "unknown-invocation")
        reference = state.variables.get("_task_reference", "unknown-task")
        return f"{invocation}:{reference}:{uuid4().hex[:12]}"
