"""Test worker: exit after SDK commit, before common catalog completion."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.execution_authority import ExecutionAuthority, authority_scope
from open_workflow_agent.security import static_principal
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


async def main() -> None:
    parameters = json.loads(Path(sys.argv[1]).read_text())
    config = RuntimeConfig.model_validate(parameters["config"])
    services = RuntimeServices(config, model=FakeModel(), database_root=parameters["root"])
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    provider = engine.native_memory.providers["notes"]
    backend = services.memory_writes._backends[provider.binding]

    async def crash_after_commit(request, handle):
        await backend.apply(request, handle)
        os._exit(0)

    services.memory_writes.register(replace(backend, apply=crash_after_commit))
    handle = services.invocations.get(parameters["invocation"])
    plan = compile_workflow(
        parameters["workflow"], enabled_function_calls=services.workflow_function_calls
    )
    authority = ExecutionAuthority.capture(
        static_principal(config.security.profile("actor-alice")), security_profile="actor-alice"
    )
    with authority_scope(authority):
        await engine.invoke(plan, handle, {})
    raise AssertionError("worker did not exit at the native commit boundary")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
