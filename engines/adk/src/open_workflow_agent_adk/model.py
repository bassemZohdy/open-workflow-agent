"""ADK-side model adapter over the common model contract."""

from __future__ import annotations

from typing import Any

from open_workflow_agent.catalog import FakeModel, Model
from open_workflow_agent.config import ModelConfig


class AdkModelAdapter:
    def __init__(self, config: ModelConfig, model: Model | None = None) -> None:
        self.config = config
        self.model = model or FakeModel()

    async def complete(self, prompt: Any) -> Any:
        return await self.model.complete(prompt, options=self.config.options)
