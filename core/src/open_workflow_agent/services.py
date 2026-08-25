"""Composition root for common runtime services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import FakeModel, FunctionCatalog, Model
from .config import RuntimeConfig
from .knowledge import KnowledgeService
from .memory import MemoryService
from .persistence import InvocationStore
from .protocols import ProtocolServices
from .tools import ToolRegistry


class RuntimeServices:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        model: Model | None = None,
        database_root: str | Path | None = None,
    ) -> None:
        self.config = config
        self.model = model or FakeModel()
        self.agent_instruction = config.agent.instruction
        self.protocols = ProtocolServices()
        self.tools = ToolRegistry.from_config(config.tools, self.protocols)
        self.agent_tools = ("search_knowledge", *self.tools.names())
        root = Path(database_root) if database_root else None
        knowledge_path = root / "knowledge.sqlite3" if root else config.knowledge.database
        memory_path = root / "memory.sqlite3" if root else config.memory.database
        invocation_path = root / "runtime.sqlite3" if root else config.persistence.database
        self.knowledge = KnowledgeService(
            config.knowledge.path,
            knowledge_path,
            chunk_size=config.knowledge.chunk_size,
            chunk_overlap=config.knowledge.chunk_overlap,
        )
        memory_database = (
            memory_path if config.memory.enabled is True or config.persistence.datasource else None
        )
        self.memory = MemoryService(str(memory_database) if memory_database else None)
        self.invocations = InvocationStore(invocation_path)
        self.catalog = FunctionCatalog.default(
            self.model, instruction=self.agent_instruction, services=self
        )
        self.catalog.register("search_knowledge", self._search_knowledge)

    async def _search_knowledge(self, payload: Any, _context: Any) -> Any:
        query = payload.get("query", "") if isinstance(payload, dict) else str(payload)
        limit = int(payload.get("limit", 5)) if isinstance(payload, dict) else 5
        return self.knowledge.search(query, limit)

    async def call_protocol(self, protocol: str, payload: Any) -> Any:
        return await self.protocols.call(protocol, payload)

    def close(self) -> None:
        self.knowledge.close()
        self.memory.close()
        self.invocations.close()
