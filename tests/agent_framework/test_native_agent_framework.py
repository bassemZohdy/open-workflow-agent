from __future__ import annotations

from typing import Any

import pytest
from open_workflow_agent_agent_framework.native import (
    AGENT_FRAMEWORK_AVAILABLE,
    AgentFrameworkNativeAdapter,
)


@pytest.mark.asyncio
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="agent-framework-core is not installed")
async def test_real_agent_framework_workflow_executes_common_runner() -> None:
    async def runner(value: Any) -> Any:
        return {"native": True, "input": value}

    adapter = AgentFrameworkNativeAdapter()
    assert adapter.available is True
    result = await adapter.invoke(runner, {"question": "hello"})
    assert result == {"native": True, "input": {"question": "hello"}}
