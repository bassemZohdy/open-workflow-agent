"""A physical diagnostic producer; the parent kills it after a committed event."""

import json
import sys

from open_workflow_agent.config import LifecycleHistoryConfig, TracingConfig
from open_workflow_agent.lifecycle_history import LifecycleHistory
from open_workflow_agent.observability import WorkflowEvent
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.tracing import ExecutionTracer


def main() -> None:
    request = json.loads(sys.stdin.readline())
    history = LifecycleHistory(
        request["database"],
        LifecycleHistoryConfig(**request["config"]),
        namespace=request["namespace"],
    )
    event = WorkflowEvent(
        "WorkflowCompleted",
        invocation_id="physical-history",
        event_id=request["event_id"],
        engine="portable",
        status="completed",
        error={"code": "controlled", "message": "must-not-persist"},
    )
    tracer = ExecutionTracer(TracingConfig(enabled=True))
    handle = ExecutionHandle("physical-history", "portable", "private", None, "s", "w", "1", "fp")
    with tracer.invocation_scope(handle):
        history.append(event.to_cloud_event())
    print("committed", flush=True)
    sys.stdin.readline()
    history.close()


if __name__ == "__main__":
    main()
