"""Reserve a private branch and wait for an external process-stop test."""

import asyncio
import os
import sys
import time
from pathlib import Path

from native_stream_test_support import authority, configuration
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.native_lineage_preflight import prepare_native_lineage
from open_workflow_agent_langgraph.native_lineage_reservation import NativeBranchReservations


async def main():
    root, source_id, target_id, request_id, reference = sys.argv[1:]
    config = configuration()
    config.observability.native_streams = None
    config.execution_authorization.policy.rules[0].actions.extend(["state.branch", "state.replay"])
    datasource = os.getenv("OWA_TEST_POSTGRES_URL")
    if datasource:
        config.persistence.datasource = datasource
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else Path(root)
    )
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    source = services.invocations.get(source_id)
    assert source is not None
    actor = authority(services, "alice")
    prepared = await prepare_native_lineage(
        services,
        source,
        actor,
        kind="branch",
        target_invocation_id=target_id,
        request_id=request_id,
        source_reference=reference,
        task_reference="/do/2/second",
        values={"value": 7},
    )
    plan = await services.recovery_definitions.resolve_inspection(source)
    target = NativeBranchReservations(services).reserve(source, actor, plan, prepared)
    print("BRANCH_RESERVED " + target.invocation_id, flush=True)
    time.sleep(60)
    raise RuntimeError("reservation worker was not stopped")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
