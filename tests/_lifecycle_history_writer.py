"""A physical diagnostic producer; the parent kills it after a committed event."""

import json
import sys

from open_workflow_agent.config import LifecycleHistoryConfig
from open_workflow_agent.lifecycle_history import LifecycleHistory
from open_workflow_agent.observability import WorkflowEvent


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
    history.append(event.to_cloud_event())
    print("committed", flush=True)
    sys.stdin.readline()
    history.close()


if __name__ == "__main__":
    main()
