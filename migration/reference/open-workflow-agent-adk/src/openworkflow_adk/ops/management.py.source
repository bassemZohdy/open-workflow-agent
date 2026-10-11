"""Management-plane API shared by a CLI, UI, or HTTP adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from openworkflow_adk.devtools.diagnostics import workflow_plan
from openworkflow_adk.ops.history import InMemoryRunHistory, SQLiteRunHistory
from openworkflow_adk.registry import WorkflowRegistry
from openworkflow_adk.runtime import run_workflow


class WorkflowManager:
    """List, inspect, plan, and execute registered workflows."""

    def __init__(
        self,
        registry: WorkflowRegistry,
        *,
        history: InMemoryRunHistory | SQLiteRunHistory | None = None,
    ) -> None:
        self.registry = registry
        self.history = history

    def list_workflows(self) -> list[dict[str, str]]:
        documents = self.registry.documents()
        return [
            {
                "namespace": document.document.namespace,
                "name": document.document.name,
                "version": document.document.version,
                "summary": document.document.summary or "",
            }
            for document in documents
        ]

    def plan(self, namespace: str, name: str, version: str = "latest") -> dict[str, Any]:
        return workflow_plan(self.registry.resolve(namespace, name, version))

    def inspect_run(self, run_id: str):
        if self.history is None:
            raise RuntimeError("run inspection requires a history backend")
        return self.history.get(run_id)

    async def run(
        self,
        namespace: str,
        name: str,
        version: str = "latest",
        *,
        input: dict[str, Any] | None = None,
        run_id: str = "managed-run",
        **kwargs: Any,
    ) -> list[Any]:
        return await run_workflow(
            self.registry.resolve(namespace, name, version),
            input,
            history=self.history,
            session_id=run_id,
            **kwargs,
        )

    async def stream_run(
        self,
        namespace: str,
        name: str,
        version: str = "latest",
        *,
        input: dict[str, Any] | None = None,
        run_id: str = "managed-run",
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Yield run events as they arrive for a live inspector."""
        queue: asyncio.Queue[Any] = asyncio.Queue()
        task = asyncio.create_task(
            self.run(
                namespace,
                name,
                version,
                input=input,
                run_id=run_id,
                event_sink=queue.put,
                **kwargs,
            )
        )
        try:
            while not task.done() or not queue.empty():
                if queue.empty():
                    await asyncio.sleep(0)
                    continue
                yield await queue.get()
            await task
        finally:
            if not task.done():
                task.cancel()
