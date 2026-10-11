"""OpenTelemetry hooks for workflow and task execution."""

from __future__ import annotations

from typing import Any

from opentelemetry import trace


class WorkflowTelemetry:
    """Record a workflow span and one child span for each emitted task event."""

    def __init__(self, tracer_name: str = "openworkflow_adk") -> None:
        self.tracer = trace.get_tracer(tracer_name)

    def record_run(self, workflow: str, run_id: str, events: list[Any]) -> None:
        with self.tracer.start_as_current_span(
            "workflow.run", attributes={"workflow.name": workflow, "workflow.run_id": run_id}
        ):
            for event in events:
                task = event.author or "unknown"
                with self.tracer.start_as_current_span(
                    "workflow.task",
                    attributes={
                        "workflow.name": workflow,
                        "workflow.run_id": run_id,
                        "workflow.task": task,
                    },
                ):
                    pass
