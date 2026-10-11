import pytest

from openworkflow_adk import InMemoryRunHistory, WorkflowRegistry, load
from openworkflow_adk.internal import InMemoryBroker, WorkflowWorker


async def test_worker_dispatches_registered_workflow_job() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "job",
                "version": "1.0.0",
            },
            "do": [{"done": {"set": {"completed": '"yes"'}}}],
        }
    )
    broker = InMemoryBroker()
    history = InMemoryRunHistory()
    await broker.publish(
        {
            "type": "workflow.run",
            "data": {
                "namespace": "demo",
                "name": "job",
                "version": "1.0.0",
                "run_id": "job-1",
            },
        }
    )

    await WorkflowWorker(WorkflowRegistry([document]), broker, history=history).run_once()

    assert history.get("job-1").status == "completed"
    assert history.get("job-1").state["completed"] == "yes"


async def test_worker_rejects_cross_region_job() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "regional-job",
                "version": "1.0.0",
            },
            "do": [{"done": {"set": {"ok": '"yes"'}}}],
        }
    )
    broker = InMemoryBroker()
    await broker.publish(
        {
            "type": "workflow.run",
            "data": {"namespace": "demo", "name": "regional-job", "region": "eu-west"},
        }
    )

    with pytest.raises(RuntimeError, match="does not match"):
        await WorkflowWorker(WorkflowRegistry([document]), broker, region="us-east").run_once()
