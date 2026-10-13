"""Composition root for common runtime services."""

from __future__ import annotations

import copy
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ._event_triggers import EventTriggerStore
from .a2a_admission import A2AAgentCardAdmission
from .a2a_client import A2ACardSelection
from .approvals import ApprovalEventBus, ApprovalService
from .catalog import FakeModel, FunctionCatalog, LiteLLMModel, Model
from .child_waits import ChildWaitStore
from .children import ChildWorkflowStore
from .config import ExternalCatalogConfig, RuntimeConfig
from .durable_events import DurableEventBus
from .effects import EffectJournal
from .errors import ConfigurationError
from .event_store import EventStore, EventStoreLimits
from .events import EventBus, InMemoryEventBus
from .external_catalog import (
    ExternalCatalogResolver,
    _function_uri,
    _validate_endpoint,
    parse_catalog_function_reference,
)
from .external_recovery import HttpExternalActionRecovery, TrustedHttpReceiver
from .forks import ForkDecisionStore
from .initial_delivery import InitialDeliveryStore
from .knowledge import FastEmbedEmbeddingProvider, KnowledgeService
from .memory import MemoryService
from .metrics import Metrics, MetricsEventSink
from .observability import EventSink, InMemoryEventSink, LifecycleCloudEventSink
from .ownership import ExecutionOwnershipStore
from .persistence import ExecutionHandle, InvocationStore
from .protocols import ProtocolServices, workflow_protocol_payload
from .recovery_definitions import RecoveryDefinitionArchive, recovery_profile
from .resources import materialize_schema_resources, schema_registry
from .sandbox import (
    InternalSandboxBackend,
    SandboxBackend,
    SandboxManager,
    validate_sandbox_capabilities,
)
from .sandbox.backends.docker import DockerSandboxBackend
from .sandbox.backends.kubernetes import KubernetesSandboxBackend
from .scheduling import ScheduleStore
from .storage import ensure_storage_namespace, namespaced_datasource, resolve_datasource
from .timers import TimerStore
from .tools import AgentToolBinding, ToolRegistry
from .workflow import WorkflowPlan
from .workflow_catalog import WorkflowCatalog


class RuntimeServices:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        model: Model | None = None,
        database_root: str | Path | None = None,
        event_sink: EventSink | None = None,
        event_bus: EventBus | None = None,
        trusted_http_receivers: tuple[TrustedHttpReceiver, ...] = (),
        recovery_profile_provider: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        """Compose runtime services with an optional nonsecret deployment revision.

        recovery_profile_provider returns only {"id": reference, "version": revision}.
        The declaration attests opaque option semantics; installed artifact,
        dependency, backend and receiver trust evidence remains mandatory.
        """
        # This callback declares a nonsecret deployment binding revision only.
        # It cannot replace installed artifact, SDK, backend or trust evidence.
        self._recovery_profile_provider = recovery_profile_provider
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
        self.metrics = Metrics()
        self.events = event_sink or InMemoryEventSink()
        self.lifecycle_events = LifecycleCloudEventSink(MetricsEventSink(self.events, self.metrics))
        self.workflow_catalog = WorkflowCatalog()
        self.external_catalogs = ExternalCatalogResolver(
            config.workflow.external_catalogs,
            event_sink=self.events,
            security=config.security,
            resource_policies=config.workflow.external_resources,
        )
        self.workflow_runner: Any = None
        self._workflow_initial_child_preflight: (
            Callable[[ExecutionHandle], Awaitable[None]] | None
        ) = None
        self.protocols = ProtocolServices(
            security=config.security,
            security_profile=config.protocols.security_profile,
            a2a_credential_endpoints=config.protocols.a2a_credential_endpoints,
        )
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
        self.protocols.sandbox = self.sandbox
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
        self.invocations = InvocationStore(invocation_path)
        self.execution_ownership = ExecutionOwnershipStore(invocation_path)
        self.fork_decisions = ForkDecisionStore(invocation_path)
        self.effect_journal = EffectJournal(invocation_path, invocations=self.invocations)
        self.external_action_recovery: HttpExternalActionRecovery | None = (
            HttpExternalActionRecovery(
                self.effect_journal,
                self.invocations,
                self.protocols.http,
                trusted_http_receivers,
            )
            if trusted_http_receivers
            else None
        )
        self.children = ChildWorkflowStore(
            self.effect_journal, self.invocations, config.child_workflows
        )
        self.child_waits = ChildWaitStore(self.effect_journal, self.invocations)
        self.initial_deliveries = InitialDeliveryStore(
            self.effect_journal,
            self.invocations,
            max_pending=config.a2a.max_pending_tasks,
        )
        self.timers = TimerStore(
            self.effect_journal, max_pending=config.execution_timing.max_pending_timers
        )
        self._event_triggers: dict[str, EventTriggerStore] = {}
        self.event_store: EventStore | None = None
        if event_bus is not None:
            raw_event_bus = event_bus
        elif config.events.backend == "memory":
            raw_event_bus = InMemoryEventBus()
        else:
            limits = EventStoreLimits(
                **config.events.model_dump(exclude={"backend", "poll_interval_seconds"})
            )
            self.event_store = EventStore(self.effect_journal, limits=limits)
            raw_event_bus = DurableEventBus(
                self.event_store, poll_interval_seconds=config.events.poll_interval_seconds
            )
        self.approvals = ApprovalService(
            invocation_path,
            enabled=config.approvals.enabled,
            operator_security_profile=config.approvals.operator_security_profile,
            security=config.security,
            event_bus=raw_event_bus,
            connection=self.effect_journal.connection if self.event_store is not None else None,
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
        self.schedules = ScheduleStore(invocation_path, invocations=self.invocations)
        self.catalog = FunctionCatalog.default(
            self.model, instruction=self.agent_instruction, services=self
        )
        self.catalog.register("search_knowledge", self._search_knowledge)
        if self.memory_enabled:
            self.catalog.register("add_memory", self._add_memory)
            self.catalog.register("search_memory", self._search_memory)
            self.catalog.register("delete_memory", self._delete_memory)
        self.recovery_definitions = RecoveryDefinitionArchive(
            self.effect_journal,
            self.invocations,
            profile_provider=self._recovery_profile,
            trust_verifier=self._verify_archived_definition_trust,
        )
        self.a2a_agent_card_admission = A2AAgentCardAdmission(self)

    def _recovery_profile(self, engine: str) -> dict[str, Any]:
        value = copy.deepcopy(
            recovery_profile(
                engine, backend=self.effect_journal.connection.kind, config=self.config
            )
        )
        # Compilation and dispatch use the composed resolver. Pin that same
        # deployment-owned policy map, including injected resolvers, rather
        # than a potentially different configuration used at construction.
        policy = value.setdefault("policy", {})
        policy["catalogs"] = {
            name: ExternalCatalogConfig.model_validate(declaration).model_dump(mode="json")
            for name, declaration in self.external_catalogs.policies.items()
        }
        policy["schemas"] = {
            name: ExternalCatalogConfig.model_validate(declaration).model_dump(mode="json")
            for name, declaration in self.external_catalogs.schemas.policies.items()
        }
        recovery = self.external_action_recovery
        value["trusted_http_receivers"] = (
            [
                recovery._receiver(receiver)
                for receiver in sorted(recovery.receivers, key=lambda r: r.id)
            ]
            if recovery is not None
            else []
        )
        if self._recovery_profile_provider is not None:
            declaration = self._recovery_profile_provider(engine)
            if (
                not isinstance(declaration, dict)
                or set(declaration) != {"id", "version"}
                or not isinstance(declaration["id"], str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", declaration["id"])
                or not isinstance(declaration["version"], str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}", declaration["version"])
            ):
                raise ConfigurationError("recovery deployment binding declaration is invalid")
            value["deployment_binding"] = {
                "profile": "owa/deployment-binding/v1",
                "id": declaration["id"],
                "version": declaration["version"],
            }
            # Deployment ownership attests opaque option semantics under this
            # revision. Meaningful option changes require a new revision; option
            # values and credentials are never retained as manifest evidence.
            value["continuation_supported"] = True
        return value

    def resolve_original_definition(self, handle: ExecutionHandle) -> WorkflowPlan:
        """Resolve admitted source without resource fetching or SDK initialization."""
        return self.recovery_definitions.resolve(handle)

    def _verify_archived_definition_trust(self, plan: WorkflowPlan) -> None:
        self.external_catalogs.schemas.verify(plan.resources)
        validate_sandbox_capabilities(
            materialize_schema_resources(plan.source),
            sandbox=self.config.sandbox,
            trusted_catalogs=self.external_catalogs.policies,
            _schema_registry=schema_registry(plan.resources),
        )
        for binding in plan.imports:
            reference = parse_catalog_function_reference(binding.reference)
            if reference is None:
                raise ValueError("archived catalog reference is invalid")
            policy = self.external_catalogs._policy(reference.catalog)
            _validate_endpoint(binding.resource_uri, policy)
            if policy.allowed_endpoints and not any(
                _function_uri(endpoint, reference) == binding.resource_uri
                for endpoint in policy.allowed_endpoints
            ):
                raise ValueError("archived catalog binding is no longer approved")
            self.external_catalogs._verify_pin(
                binding.digest, policy, binding.reference, binding.resource_uri
            )

    def event_triggers(
        self, engine: str, *, clock: Callable[[], float] | None = None
    ) -> EventTriggerStore | None:
        if self.event_store is None:
            return None
        store = self._event_triggers.get(engine)
        if store is None:
            store = EventTriggerStore(
                self.event_store,
                self.schedules,
                engine=engine,
                clock=clock if clock is not None else time.time,
                filter_guard=(
                    self.event_bus.event_filter_allowed
                    if isinstance(self.event_bus, ApprovalEventBus)
                    else None
                ),
            )
            self._event_triggers[engine] = store
        return store

    def engine_database_path(self, engine: str, *, create_namespace: bool = True) -> str:
        """Return the native persistence path without exposing engine state publicly."""

        if self.database_root:
            filename = {
                "adk": "adk-sessions.sqlite3",
                "langgraph": "langgraph-checkpoints.sqlite3",
            }.get(engine, f"{engine}-persistence.sqlite3")
            return str(self.database_root / filename)
        if self.datasource:
            if create_namespace:
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

    async def call_protocol(
        self, protocol: str, payload: Any, *, _a2a_selection: A2ACardSelection | None = None
    ) -> Any:
        prepared = workflow_protocol_payload(protocol, payload)
        if _a2a_selection is None:
            return await self.protocols.call(protocol, prepared)
        return await self.protocols.call(
            protocol,
            prepared,
            _a2a_selection=_a2a_selection,
        )

    def close(self) -> None:
        self.knowledge.close()
        self.memory.close()
        self.schedules.close()
        self.invocations.close()
        self.execution_ownership.close()
        self.fork_decisions.close()
        self.approvals.close()
        if self.event_store is not None:
            self.event_store.close()
        self.effect_journal.close()
