"""Exit-test worker paused before or after the real SDK transaction commit."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from open_workflow_agent.ownership import ExecutionLease, NativeWriteFence
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_langgraph.native_state_mutations import (
    NativeStateWrite,
    apply_native_state_update,
)
from test_native_state_mutations import update


async def main() -> None:
    value = json.loads(Path(sys.argv[1]).read_text())
    ready = Path(sys.argv[2])

    async def paused(saver, config):
        result = await update(saver, config)
        if not value.get("commit_before_ready", False):
            ready.write_text("sdk-written-before-commit")
            await asyncio.Event().wait()
        return result

    await apply_native_state_update(
        value["database"],
        NativeWriteFence(value["common"], ExecutionLease(**value["lease"])),
        ExecutionHandle(**value["handle"]),
        compile_workflow(value["workflow"]),
        NativeStateWrite(**value["request"]),
        paused,
    )
    ready.write_text("sdk-committed-before-common-acknowledgement")
    await asyncio.Event().wait()


with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
    runner.run(main())
