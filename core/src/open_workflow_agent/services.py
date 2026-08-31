"""Composition root for common runtime services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .approvals import ApprovalService
from .catalog import FakeModel, FunctionCatalog, LiteLLMModel, Model
from .config import RuntimeConfig
from .events import EventBus, InMemoryEventBus
from .external_catalog import ExternalCatalogResolver
from .knowledge import FastEmbedEmbeddingProvider, KnowledgeService
from .memory import MemoryService
from .observability import EventSink, InMemoryEventSink, LifecycleCloudEventSink
from .persistence import InvocationStore
from .protocols import ProtocolServices
from .sandbox import InternalSandboxBackend, SandboxBackend, SandboxManager
from .sandbox.backends.docker import DockerSandboxBackend
from .sandbox.backends.kubernetes import KubernetesSandboxBackend
from .scheduling import ScheduleStore
from .storage import ensure_storage_namespace, namespaced_datasource, resolve_datasource
from .tools import AgentToolBinding, ToolRegistry
from .workflow_catalog import WorkflowCatalog

_OPEN_WORKFLOW_A2A_METHODS = {
    "message/send": "SendMessage",
    "message/stream": "SendStreamingMessage",
    "tasks/get": "GetTask",
    "tasks/list": "ListTasks",
    "tasks/cancel": "CancelTask",
    "tasks/resubscribe": "SubscribeToTask",
    "tasks/pushNotificationConfig/set": "CreateTaskPushNotificationConfig",
    "tasks/pushNotificationConfig/get": "GetTaskPushNotificationConfig",
    "tasks/pushNotificationConfig/list": "ListTaskPushNotificationConfigs",
    "tasks/pushNotificationConfig/delete": "DeleteTaskPushNotificationConfig",
    "agent/getAuthenticatedExtendedCard": "GetExtendedAgentCard",
}


class RuntimeServices:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        model: Model | None = None,
        database_root: str | Path | None = None,
        event_sink: EventSink | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.config = config
        self.model = model or (
            FakeModel()
            if config.model.provider == "fake"
            else LiteLLMModel(
                config.model.name,
                temperature=config.model.temperature,
                options=config.model.options,
            )
        )
        self.agent_instruction = config.agent.instruction
        self.database_root = Path(database_root) if database_root else None
        self.datasource = resolve_datasource(config.persistence.datasource)
        self.events = event_sink or InMemoryEventSink()
        self.lifecycle_events = LifecycleCloudEventSink(self.events)
        raw_event_bus = event_bus or InMemoryEventBus()
        self.workflow_catalog = WorkflowCatalog()
        self.external_catalogs = ExternalCatalogResolver(
            config.workflow.external_catalogs, event_sink=self.events, security=config.security
        )
        self.workflow_runner: Any = None
        self.protocols = ProtocolServices()
        self.tools = ToolRegistry.from_config(
            config.tools, self.protocols, security=config.security
        )
        sandbox_backend: SandboxBackend
        if config.sandbox.backend == "docker":
            sandbox_backend = DockerSandboxBackend(config.sandbox)
        elif config.sandbox.backend == "kubernetes":
            sandbox_backend = KubernetesSandboxBackend(config.sandbox)
        else:
            sandbox_backend = InternalSandboxBackend(config.sandbox)
        self.sandbox = SandboxManager(sandbox_backend)
        self.memory_enabled = config.memory.enabled is not False
        memory_tools = (
            ("add_memory", "search_memory", "delete_memory") if self.memory_enabled else ()
        )
        self.agent_tools = ("search_knowledge", *memory_tools, *self.tools.names())
        root = self.database_root
        knowledge_path: str | Path
        memory_path: str | Path
        invocation_path: str | Path
        if root:
            knowledge_path = root / "knowledge.sqlite3"
            memory_path = root / "memory.sqlite3"
            invocation_path = root / "runtime.sqlite3"
        elif self.datasource:
            knowledge_path = self.datasource
            memory_path = self.datasource
            invocation_path = self.datasource
        else:
            knowledge_path = config.knowledge.database
            memory_path = config.memory.database
            invocation_path = config.persistence.database
        self.approvals = ApprovalService(
            invocation_path,
            enabled=config.approvals.enabled,
            operator_security_profile=config.approvals.operator_security_profile,
            security=config.security,
            event_bus=raw_event_bus,
        )
        self.event_bus = self.approvals.event_bus
        self.knowledge = KnowledgeService(
            config.knowledge.path,
            knowledge_path,
            embedding=FastEmbedEmbeddingProvider(
                model_name=config.embedding.model,
                model_revision=config.embedding.revision,
            ),
            chunk_size=config.knowledge.chunk_size,
            chunk_overlap=config.knowledge.chunk_overlap,
        )
        memory_database = (
            memory_path
            if self.memory_enabled
            and (root is not None or config.memory.enabled is True or config.persistence.datasource)
            else None
        )
        self.memory = MemoryService(str(memory_database) if memory_database else None)
        self.invocations = InvocationStore(invocation_path)
        self.schedules = ScheduleStore(invocation_path)
        self.catalog = FunctionCatalog.default(
            self.model, instruction=self.agent_instruction, services=self
        )
        self.catalog.register("search_knowledge", self._search_knowledge)
        if self.memory_enabled:
            self.catalog.register("add_memory", self._add_memory)
            self.catalog.register("search_memory", self._search_memory)
            self.catalog.register("delete_memory", self._delete_memory)

    def engine_database_path(self, engine: str) -> str:
        """Return the native persistence path without exposing engine state publicly."""

        if self.database_root:
            filename = {
                "adk": "adk-sessions.sqlite3",
                "langgraph": "langgraph-checkpoints.sqlite3",
            }.get(engine, f"{engine}-persistence.sqlite3")
            return str(self.database_root / filename)
        if self.datasource:
            ensure_storage_namespace(self.datasource, f"owa_{engine}")
            return namespaced_datasource(self.datasource, f"owa_{engine}")
        filename = {
            "adk": "adk-sessions.sqlite3",
            "langgraph": "langgraph-checkpoints.sqlite3",
        }.get(engine, f"{engine}-persistence.sqlite3")
        return str(Path(self.config.persistence.database).with_name(filename))

    async def _search_knowledge(self, payload: Any, _context: Any) -> Any:
        query = payload.get("query", "") if isinstance(payload, dict) else str(payload)
        limit = int(payload.get("limit", 5)) if isinstance(payload, dict) else 5
        return self.knowledge.search(query, limit)

    async def _add_memory(self, payload: Any, _context: Any) -> dict[str, int]:
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise ValueError("add_memory requires a text string")
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("add_memory metadata must be an object")
        return {"id": self.memory.add(payload["text"], metadata)}

    async def _search_memory(self, payload: Any, _context: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            payload = {"query": str(payload)}
        query = payload.get("query", "")
        limit = int(payload.get("limit", 10))
        if not isinstance(query, str) or limit < 1 or limit > 100:
            raise ValueError("search_memory requires a query and a limit between 1 and 100")
        return self.memory.search(query, limit)

    async def _delete_memory(self, payload: Any, _context: Any) -> dict[str, bool]:
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), int):
            raise ValueError("delete_memory requires an integer id")
        return {"deleted": self.memory.delete(payload["id"])}

    async def invoke_agent_tool(self, name: str, payload: Any) -> Any:
        if name == "search_knowledge":
            return await self._search_knowledge(payload, None)
        if name == "add_memory":
            return await self._add_memory(payload, None)
        if name == "search_memory":
            return await self._search_memory(payload, None)
        if name == "delete_memory":
            return await self._delete_memory(payload, None)
        return await self.tools.invoke(name, payload)

    def agent_tool_bindings(self) -> tuple[AgentToolBinding, ...]:
        bindings: list[AgentToolBinding] = [
            AgentToolBinding(
                name="search_knowledge",
                description="Search mounted knowledge documents.",
                invoke=lambda payload: self._search_knowledge(payload, None),
            )
        ]
        if self.memory_enabled:
            bindings.extend(
                [
                    AgentToolBinding(
                        name="add_memory",
                        description="Store durable agent memory with optional metadata.",
                        invoke=lambda payload: self._add_memory(payload, None),
                    ),
                    AgentToolBinding(
                        name="search_memory",
                        description="Search long-term agent memory.",
                        invoke=lambda payload: self._search_memory(payload, None),
                    ),
                    AgentToolBinding(
                        name="delete_memory",
                        description="Delete a long-term agent memory by id.",
                        invoke=lambda payload: self._delete_memory(payload, None),
                    ),
                ]
            )
        bindings.extend(self.tools.bindings())
        return tuple(bindings)

    async def call_protocol(self, protocol: str, payload: Any) -> Any:
        if protocol == "a2a" and isinstance(payload, dict):
            method = payload.get("method")
            if isinstance(method, str) and method in _OPEN_WORKFLOW_A2A_METHODS:
                payload = {**payload, "method": _OPEN_WORKFLOW_A2A_METHODS[method]}
        return await self.protocols.call(protocol, payload)

    def close(self) -> None:
        self.knowledge.close()
        self.memory.close()
        self.schedules.close()
        self.invocations.close()
        self.approvals.close()
