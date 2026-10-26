"""Exit without cleanup after a durable wait and process-local observations."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "core/src"), str(ROOT / "engines/langgraph/src")]


async def run() -> None:
    from native_stream_test_support import authority, workflow
    from open_workflow_agent.catalog import FakeModel
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.execution_authority import authority_scope
    from open_workflow_agent.services import RuntimeServices
    from open_workflow_agent_langgraph import LangGraphWorkflowEngine
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

    original = LangGraphScopeExecutor._invoke_scope_graph

    async def diagnostic(self, *args, **kwargs):
        try:
            return await original(self, *args, **kwargs)
        except Exception as exc:
            import traceback

            # Only this controlled test worker exposes underlying SDK diagnostics.
            traceback.print_exception(exc, limit=6, file=sys.stderr)
            raise

    LangGraphScopeExecutor._invoke_scope_graph = diagnostic
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    config = RuntimeConfig.model_validate(request["config"])
    services = RuntimeServices(config, model=FakeModel(), database_root=request["root"])
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    plan = workflow(waiting=True)
    handle = services.invocations.create(
        engine="langgraph",
        session_id="same",
        user_id="same",
        owner_principal="alice",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    with authority_scope(authority(services, "alice")):
        result = await engine.invoke(plan, handle, {"value": "worker"})
    assert result.status == "waiting", result.error
    frames, _ = services.native_streams.subscribe(handle)
    assert frames and len(services.model.calls) == 1
    with Path(sys.argv[2]).open("w", encoding="utf-8") as output:
        json.dump({"invocation_id": handle.invocation_id, "cursor": frames[-1].event_id}, output)
        output.flush()
        os.fsync(output.fileno())
    os._exit(0)


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run())
