"""Composition root for common runtime services."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from ._event_triggers import EventTriggerStore
from .a2a_admission import A2AAgentCardAdmission
from .a2a_client import A2ACardSelection
from .agent_roles import RoleBindings, authorize_role_action
from .approval_dispatcher import ApprovalExpiryDispatcher
from .approvals import ApprovalEventBus, ApprovalService
from .bounded_memory import BoundedMemoryService
from .catalog import BoundedModel, FakeModel, FunctionCatalog, LiteLLMModel, Model
from .child_waits import ChildWaitStore
from .children import ChildWorkflowStore
from .config import ExternalCatalogConfig, RuntimeConfig
from .durable_events import DurableEventBus
from .effects import EffectIdentity, EffectJournal, effect_request_digest
from .errors import ConfigurationError, UnsupportedWorkflowFeature
from .event_store import EventStore, EventStoreLimits
from .event_transport import EmbeddedEventTransport
from .events import EventBus, InMemoryEventBus
from .execution_authorization import ExecutionAuthorizationStore
from .execution_context import ExecutionActionContext, action_scope, current_action_context
from .external_catalog import (
    ExternalCatalogResolver,
    _function_uri,
    _validate_endpoint,
    parse_catalog_function_reference,
)
from .external_recovery import HttpExternalActionRecovery, TrustedHttpReceiver
from .forks import ForkDecisionStore
from .grpc_client import GrpcRegistry
from .grpc_client import verify_sdk as verify_grpc_sdk
from .initial_delivery import InitialDeliveryStore
from .knowledge import FastEmbedEmbeddingProvider, KnowledgeService
from .memory import MemoryService
from .metrics import Metrics, MetricsEventSink
from .observability import EventSink, InMemoryEventSink, LifecycleCloudEventSink
from .openapi_admission import OpenApiOperationAdmission
from .openapi_client import OpenApiOperationSelection
from .ownership import ExecutionLease, ExecutionOwnershipStore
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
from .sandbox.job_dispatcher import SandboxJobDispatcher
from .sandbox.jobs import SandboxJobStore
from .scheduling import ScheduleClaim, ScheduleRecord, ScheduleStore
from .security import BearerSecurityProfile, ProfileAuthentication, static_principal
from .storage import (
    StorageConnection,
    ensure_storage_namespace,
    namespaced_datasource,
    resolve_datasource,
)
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
        role_models: Mapping[str, Model] | None = None,
        database_root: str | Path | None = None,
        event_sink: EventSink | None = None,
        event_bus: EventBus | None = None,
        embedded_event_transport: bool = False,
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
        self._physical_profile_slots = asyncio.Semaphore(2)
        self.config = config
        if type(embedded_event_transport) is not bool or (
            embedded_event_transport
            and (event_bus is not None or config.events.backend == "memory")
        ):
            raise ConfigurationError(
                "embedded transport requires the actual durable common event store"
            )
        self._bounded_memory = config.memory.bounds is not None and config.memory.bounds.enabled
        if self._bounded_memory and (
            config.memory.enabled is False or "database" in config.memory.model_fields_set
        ):
            raise ConfigurationError(
                "bounded memory requires the common persistence connection "
                "without a legacy memory database override"
            )
        if config.sandbox.background.enabled:
            candidate = (
                Path(database_root) / "runtime.sqlite3"
                if database_root
                else (
                    resolve_datasource(config.persistence.datasource) or config.persistence.database
                )
            )
            if (
                not config.sandbox.enabled
                or config.sandbox.backend != "internal"
                or str(candidate) == ":memory:"
                or str(candidate).startswith(("postgresql:", "postgres:"))
            ):
                raise ConfigurationError(
                    "background execution requires enabled internal sandbox "
                    "and persistent embedded SQLite"
                )
        self.grpc_registry = GrpcRegistry(
            binding.to_binding(name) for name, binding in config.protocols.grpc_bindings.items()
        )
        self.grpc_enabled = bool(config.protocols.grpc_bindings)
        from .executable_sources import ExecutableSource, ExecutableSourceRegistry

        self.executable_sources = ExecutableSourceRegistry(
            tuple(
                ExecutableSource.from_value(item.model_dump(mode="json"))
                for item in config.sandbox.script_sources
            )
        )
        if self.grpc_enabled:
            verify_grpc_sdk()
        self.model = model or (
            FakeModel()
            if config.model.provider == "fake"
            else LiteLLMModel(
                config.model.name,
                temperature=config.model.temperature,
                options=config.model.options,
                credentials=config.model.credentials,
                authorize_credential=self.authorize_model_credential,
                bounds=config.model.bounds,
            )
        )
        if config.model.bounds is not None and not isinstance(self.model, LiteLLMModel):
            self.model = BoundedModel(self.model, config.model.bounds)
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
            grpc_registry=self.grpc_registry if self.grpc_enabled else None,
            grpc_credential_endpoints=config.protocols.grpc_credential_endpoints,
        )
        self.workflow_runner: Any = None
        self._workflow_initial_child_preflight: (
            Callable[[ExecutionHandle], Awaitable[None]] | None
        ) = None
        self.protocols = ProtocolServices(
            security=config.security,
            security_profile=config.protocols.security_profile,
            a2a_credential_endpoints=config.protocols.a2a_credential_endpoints,
            openapi_credential_endpoints=config.protocols.openapi_credential_endpoints,
            grpc_registry=self.grpc_registry if self.grpc_enabled else None,
            grpc_credential_endpoints=config.protocols.grpc_credential_endpoints,
            authorize_destination=self.authorize_protocol_destination,
            authorize_credential=self.authorize_protocol_credential,
        )
        self.tools = ToolRegistry.from_config(
            config.tools, self.protocols, security=config.security
        )
        self.tools.authorize = self.authorize_tool_invocation
        self.tools.authority_enabled = lambda: self.config.execution_authorization.enabled
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
        self.role_bindings = RoleBindings(self, role_models)
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
        self.event_transport = (
            EmbeddedEventTransport(self.event_store)
            if embedded_event_transport and self.event_store is not None
            else None
        )
        self.approvals = ApprovalService(
            invocation_path,
            enabled=config.approvals.enabled,
            operator_security_profile=config.approvals.operator_security_profile,
            security=config.security,
            event_bus=raw_event_bus,
            connection=self.effect_journal.connection if self.event_store is not None else None,
            protected=config.approvals.protected,
            actor_security_profiles=tuple(config.approvals.actor_security_profiles),
            invocations=self.invocations,
            max_pending=config.approvals.max_pending,
            max_policy_bytes=config.approvals.max_policy_bytes,
            scan_limit=config.approvals.scan_limit,
        )
        self._approval_expiry_dispatcher: ApprovalExpiryDispatcher | None = (
            ApprovalExpiryDispatcher(
                self.approvals,
                poll_seconds=config.approvals.poll_interval_seconds,
                scan_limit=config.approvals.scan_limit,
            )
            if config.approvals.protected
            else None
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
        self.memory: MemoryService = (
            BoundedMemoryService(self.invocations, config.memory.bounds)
            if self._bounded_memory and config.memory.bounds is not None
            else MemoryService(str(memory_database) if memory_database else None)
        )
        self.schedules = ScheduleStore(invocation_path, invocations=self.invocations)
        self.catalog = FunctionCatalog.default(
            self.model, instruction=self.agent_instruction, services=self
        )
        self.role_bindings.register(self.catalog)
        self.catalog.register("search_knowledge", self._search_knowledge)
        if self.memory_enabled:
            self.catalog.register("add_memory", self._add_memory)
            self.catalog.register("search_memory", self._search_memory)
            self.catalog.register("delete_memory", self._delete_memory)
        self.recovery_definitions = RecoveryDefinitionArchive(
            self.effect_journal,
            self.invocations,
            profile_provider=self._recovery_profile,
            async_profile_provider=self._recovery_profile_async,
            trust_verifier=self._verify_archived_definition_trust,
        )
        self.execution_authorizations = ExecutionAuthorizationStore(
            self.effect_journal,
            self.invocations,
            self.recovery_definitions,
            config_provider=lambda: self.config.execution_authorization,
            security_provider=lambda: self.config.security,
        )
        self.recovery_definitions.admission_participant = (
            self.execution_authorizations.admission_participant
        )
        self.recovery_definitions.authority_verifier = (
            self.execution_authorizations.verify_invocation
        )
        self.effect_journal._execution_authorization_seal = (
            self.execution_authorizations.seal_business
        )
        self.effect_journal._execution_authorization_intent = (
            self.execution_authorizations.seal_intent
        )
        if self.external_action_recovery is not None:
            self.external_action_recovery.authorize_lookup = self._authorize_external_lookup
        self.recovery_definitions.authority_expectation = (
            self.execution_authorizations.expected_authority_digest
        )
        self.children.authority_participant = lambda child, parent, lease: (
            self.execution_authorizations.reserve_child(child, parent, execution_lease=lease)
        )
        self.schedules.authority_expectation = (
            self.execution_authorizations.expected_schedule_authority_digest
        )
        self.schedules.authority_verifier = self._verify_schedule_authority
        self.schedules.authority_participant = lambda connection, record: (
            self.execution_authorizations.reserve_schedule(
                record, commit=False, connection=connection
            )
        )
        self.schedules.invocation_authority_participant = self._reserve_scheduled_authority
        if self.event_store is not None:
            self.event_store.outcome_participant = self.execution_authorizations.seal_business
        self.a2a_agent_card_admission = A2AAgentCardAdmission(self)
        self.openapi_operation_admission = OpenApiOperationAdmission(self)
        from .executable_source_admission import ExecutableSourceAdmission

        self.executable_source_admissions = ExecutableSourceAdmission(self, self.executable_sources)
        self.sandbox_jobs: SandboxJobStore | None = None
        self._background_dispatcher: SandboxJobDispatcher | None = None
        if config.sandbox.background.enabled:
            from .sandbox.internal_jobs import InternalSandboxJobBackend, internal_job_profile

            if self.effect_journal.connection.kind != "sqlite":
                raise ConfigurationError("background execution requires embedded SQLite")
            self.sandbox_jobs = SandboxJobStore(
                self,
                profile_provider=lambda: internal_job_profile(self.config.sandbox),
                max_pending=config.sandbox.background.max_pending,
            )
            self.sandbox_jobs.outcome_participant = self.execution_authorizations.seal_business
            job_backend = InternalSandboxJobBackend(config.sandbox, self.sandbox_jobs)
            self.sandbox = SandboxManager(sandbox_backend, job_backend=job_backend)
            self.protocols.sandbox = self.sandbox
            self.effect_journal._sandbox_job_verifier = self.sandbox_jobs.verify_receipt
            bounds = config.sandbox.background
            self._background_dispatcher = SandboxJobDispatcher(
                self.sandbox_jobs,
                self.sandbox,
                lease_seconds=bounds.lease_seconds,
                io_timeout=bounds.io_timeout,
                poll_seconds=bounds.poll_seconds,
                scan_limit=bounds.scan_limit,
            )

    def verify_background_jobs(self, handle: ExecutionHandle) -> None:
        store = self.sandbox_jobs
        if store is None:
            return
        # Index verification detects retained policies even if a job row was
        # removed; enumeration detects a removed action index independently.
        self.effect_journal.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
        rows = self.effect_journal.connection.execute(
            "SELECT job_id FROM sandbox_jobs WHERE invocation_id=? ORDER BY job_id LIMIT 10001",
            (handle.invocation_id,),
        ).fetchall()
        if len(rows) > 10000:
            raise ConfigurationError("background invocation proof capacity is exceeded")
        for row in rows:
            job = store.get(row[0])
            if job is None:
                raise ConfigurationError("background invocation admission is absent")
            store._authority(job)

    def verify_protected_approvals(self, handle: ExecutionHandle) -> None:
        store = self.approvals.protected_store
        if self.config.approvals.protected and store is None:
            raise ConfigurationError("protected approval verification is unavailable")
        if store is not None:
            store.verify_invocation(handle.invocation_id, handle.workflow_fingerprint)
        else:
            from .protected_approval_recovery import protected_approval_invocation_retained

            if protected_approval_invocation_retained(
                self.effect_journal.connection, handle.invocation_id
            ):
                raise ConfigurationError("protected approval deployment is unavailable")

    async def verify_executable_source_history(self, handle: ExecutionHandle) -> None:
        """Discover independent retained source proof before taking execution ownership."""
        from .executable_source_admission import SOURCE_POLICY

        connection = self.effect_journal.connection
        rows = connection.execute(
            "SELECT business_key,fingerprint,CASE WHEN length(payload)<=524288 "
            "THEN payload ELSE NULL END FROM executable_source_admissions "
            "WHERE invocation_id=? ORDER BY business_key LIMIT 10001",
            (handle.invocation_id,),
        ).fetchall()
        if len(rows) > 10000:
            raise ConfigurationError("executable source history capacity is exceeded")
        total = 0
        for key, fingerprint, encoded in rows:
            if encoded is None or fingerprint != handle.workflow_fingerprint:
                raise ConfigurationError("executable source history is inconsistent")
            total += len(encoded.encode("utf-8"))
            if total > 16 * 1024 * 1024:
                raise ConfigurationError("executable source history capacity is exceeded")
            try:
                value = json.loads(encoded)
                identity = EffectIdentity.from_value(value["business_identity"])
            except (ValueError, TypeError, KeyError):
                raise ConfigurationError("executable source history is inconsistent") from None
            if identity.key != key or identity.invocation_id != handle.invocation_id:
                raise ConfigurationError("executable source history is inconsistent")
            policy = replace(identity, operation=SOURCE_POLICY)
            receipt = self.effect_journal.get(policy)
            if receipt is None or receipt.status != "completed":
                raise ConfigurationError("executable source history proof is unavailable")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        indexed = connection.execute(
            "SELECT identity,CASE WHEN length(payload)<=65536 THEN payload ELSE NULL END "
            "FROM workflow_effect_invocations WHERE invocation_id=? ORDER BY identity LIMIT 10001",
            (handle.invocation_id,),
        ).fetchall()
        if len(indexed) > 10000:
            raise ConfigurationError("executable source index capacity is exceeded")
        for key, encoded in indexed:
            if encoded is None:
                raise ConfigurationError("executable source history index is inconsistent")
            total += len(encoded.encode("utf-8"))
            if total > 16 * 1024 * 1024:
                raise ConfigurationError("executable source history capacity is exceeded")
            try:
                identity = EffectIdentity.from_value(json.loads(encoded))
            except (ValueError, TypeError):
                raise ConfigurationError(
                    "executable source history index is inconsistent"
                ) from None
            if identity.operation not in {"run:script", SOURCE_POLICY}:
                continue
            if (
                identity.key != key
                or identity.invocation_id != handle.invocation_id
                or identity.workflow_fingerprint != handle.workflow_fingerprint
                or self.effect_journal.get(identity) is None
            ):
                raise ConfigurationError("executable source history proof is unavailable")
            await asyncio.sleep(0)
            await asyncio.sleep(0)

    def verify_protected_approval_plan(self, plan: WorkflowPlan) -> None:
        from .protected_approval_policy import ProtectedApprovalPolicy, original_expiry

        for scope in plan.scopes:
            for task in scope.tasks:
                emit = task.definition.get("emit")
                if not isinstance(emit, dict):
                    continue
                descriptor = emit.get("event")
                if not isinstance(descriptor, dict):
                    continue
                event = descriptor.get("with")
                if not isinstance(event, dict):
                    continue
                data = event.get("data")
                if (
                    event.get("type") != "io.openworkflow.agent.approval.requested"
                    or not isinstance(data, dict)
                    or "approvalPolicy" not in data
                ):
                    continue
                if not self.config.approvals.protected or self.approvals.protected_store is None:
                    raise UnsupportedWorkflowFeature(
                        "protected approval policy is not enabled",
                        details={"reference": task.reference},
                    )
                policy = ProtectedApprovalPolicy.from_value(data["approvalPolicy"])
                allowed = self.approvals.protected_store.actor_ids
                if not {
                    policy.assignee,
                    *policy.reassignment_targets,
                    *policy.escalation_targets,
                }.issubset(allowed):
                    raise UnsupportedWorkflowFeature(
                        "protected approval actor is not deployment-bound",
                        details={"reference": task.reference},
                    )
                due = event.get("approvalexpiresat")
                if not isinstance(due, str) or "${" not in due:
                    original_expiry(due)

    async def start_protected_approvals(self) -> None:
        if self._approval_expiry_dispatcher is not None:
            await self._approval_expiry_dispatcher.start()

    async def stop_protected_approvals(self) -> None:
        if self._approval_expiry_dispatcher is not None:
            await self._approval_expiry_dispatcher.stop()

    async def start_background_jobs(self) -> None:
        if self._background_dispatcher is not None:
            await self._background_dispatcher.start()

    async def stop_background_jobs(self) -> None:
        # Only release the observer. Independent supervisor lifetimes persist.
        if self._background_dispatcher is not None:
            await self._background_dispatcher.stop()

    def _recovery_profile(self, engine: str) -> dict[str, Any]:
        return self._compose_recovery_profile(
            engine,
            recovery_profile(
                engine, backend=self.effect_journal.connection.kind, config=self.config
            ),
        )

    async def _recovery_profile_async(self, engine: str) -> dict[str, Any]:
        """Keep bounded physical file verification separate from storage/callbacks."""
        snapshot = copy.deepcopy(self.config)
        before = self._compose_recovery_profile(engine, {})
        backend = self.effect_journal.connection.kind
        try:
            async with asyncio.timeout(30):
                await self._physical_profile_slots.acquire()
                try:
                    worker = asyncio.create_task(
                        asyncio.to_thread(
                            recovery_profile, engine, backend=backend, config=snapshot
                        )
                    )
                except BaseException:
                    self._physical_profile_slots.release()
                    raise

                def completed(task: asyncio.Task[dict[str, Any]]) -> None:
                    self._physical_profile_slots.release()
                    if not task.cancelled():
                        task.exception()

                # Cancellation stops admission immediately, but does not make room
                # for another file worker until this finite read has really ended.
                worker.add_done_callback(completed)
                physical = await asyncio.shield(worker)
        except TimeoutError:
            raise ConfigurationError("physical recovery verification timed out") from None
        if (
            self.config != snapshot
            or self.effect_journal.connection.kind != backend
            or self._compose_recovery_profile(engine, {}) != before
        ):
            raise ConfigurationError("recovery deployment binding changed during verification")
        value = self._compose_recovery_profile(engine, physical)
        if self.config != snapshot or self._compose_recovery_profile(engine, {}) != before:
            raise ConfigurationError("recovery deployment binding changed during verification")
        return value

    def _compose_recovery_profile(self, engine: str, physical: dict[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(physical)
        # Compilation and dispatch use the composed resolver. Pin that same
        # deployment-owned policy map, including injected resolvers, rather
        # than a potentially different configuration used at construction.
        policy = value.setdefault("policy", {})
        if self._bounded_memory and self.config.memory.bounds is not None:
            policy["memory_bounds"] = self.config.memory.bounds.model_dump(mode="json")
        if self.event_transport is not None:
            policy["event_transport"] = self.event_transport.descriptor()
        if self.role_function_calls:
            policy["agent_roles"] = self.role_bindings.snapshot()
        if self.config.model.credentials:
            policy.setdefault("model", {})["credentials"] = {
                name: reference.model_dump(mode="json")
                for name, reference in sorted(self.config.model.credentials.items())
            }
        if self.config.model.bounds is not None:
            policy.setdefault("model", {})["bounds"] = self.config.model.bounds.model_dump(
                mode="json"
            )
        policy["approvals"] = self.config.approvals.model_dump(mode="json")
        if self.config.approvals.protected:
            bindings = []
            for name in sorted(self.config.approvals.actor_security_profiles):
                profile = self.approvals.security.profile(name)
                if not isinstance(profile, BearerSecurityProfile) or not profile.principal:
                    raise ConfigurationError("protected approval actor binding is unavailable")
                principal = static_principal(profile)
                bindings.append(
                    {
                        "profile": name,
                        "principal": principal.identity,
                        "roles": sorted(principal.roles),
                        "scopes": sorted(principal.scopes),
                        "audience": sorted(principal.audience),
                    }
                )
            policy["approvals"]["actorBindings"] = bindings
        policy.setdefault("sandbox", {})["script_sources"] = [
            {
                "id": source.id,
                "version": source.version,
                "uri": source.uri,
                "language": source.language,
                "digest": source.digest,
            }
            for source in self.executable_sources.sources
        ]
        policy["catalogs"] = {
            name: ExternalCatalogConfig.model_validate(declaration).model_dump(mode="json")
            for name, declaration in self.external_catalogs.policies.items()
        }
        policy["schemas"] = {
            name: ExternalCatalogConfig.model_validate(declaration).model_dump(mode="json")
            for name, declaration in self.external_catalogs.schemas.policies.items()
        }
        self._verify_grpc_registry()
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
        from .executable_source_plan import capture_script_resources

        self.verify_protected_approval_plan(plan)
        for item in plan.script_resources:
            self.executable_sources.verify(item.source)
        if capture_script_resources(plan.source, self.executable_sources) != plan.script_resources:
            raise ConfigurationError("archived executable source selection differs")
        self.verify_grpc_plan(plan)
        self.external_catalogs.schemas.verify(plan.resources)
        validate_sandbox_capabilities(
            materialize_schema_resources(plan.source),
            sandbox=self.config.sandbox,
            trusted_catalogs=self.external_catalogs.policies,
            _schema_registry=schema_registry(plan.resources),
            grpc_enabled=self.grpc_enabled,
            enabled_function_calls=self.role_function_calls,
            executable_sources=self.executable_sources,
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

    def _verify_grpc_registry(self) -> None:
        if not self.grpc_enabled and not self.config.protocols.grpc_bindings:
            # A default-disabled deployment retains its existing custom common
            # service seams and does not activate a protocol SDK or registry.
            return
        expected = [
            binding.recovery_snapshot(name)
            for name, binding in sorted(self.config.protocols.grpc_bindings.items())
        ]
        if (
            self.grpc_enabled != bool(expected)
            or self.grpc_registry.snapshot() != expected
            or self.protocols.grpc_registry is not (self.grpc_registry if expected else None)
            or self.external_catalogs.grpc_registry
            is not (self.grpc_registry if expected else None)
            or self.protocols._grpc_credential_endpoints
            != self.config.protocols.grpc_credential_endpoints
            or self.external_catalogs.grpc_credential_endpoints
            != self.config.protocols.grpc_credential_endpoints
        ):
            raise ConfigurationError("gRPC deployment registry composition changed")

    def verify_grpc_plan(self, plan: WorkflowPlan) -> None:
        self._verify_grpc_registry()
        definitions = [task.definition for scope in plan.scopes for task in scope.tasks]
        definitions.extend(binding.execution_definition for binding in plan.imports)
        for definition in tuple(definitions):
            if not self.grpc_enabled:
                break
            call = definition.get("call")
            if not isinstance(call, str):
                continue
            registered = self.external_catalogs.registered_binding(call)
            if registered is not None and registered.execution_definition.get("call") == "grpc":
                definitions.append(registered.execution_definition)
        for definition in definitions:
            if definition.get("call") != "grpc":
                continue
            if not self.grpc_enabled:
                raise UnsupportedWorkflowFeature("gRPC deployment registry is not enabled")
            verify_grpc_sdk()
            value = definition.get("with")
            if not isinstance(value, dict):
                raise UnsupportedWorkflowFeature("gRPC route must be deployment-bound")
            # Arguments may contain expressions. Route selection is fixed by
            # the deployment before ownership or any provider/business call.
            self.grpc_registry.prepare({**value, "arguments": {}}, _route_only=True)

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

    async def authorize_current_action(
        self, action: str, resource: str, request_digest: str | None = None
    ) -> None:
        authorize_role_action(action, resource)
        context = current_action_context()
        if context is None:
            if self.config.execution_authorization.enabled:
                raise ConfigurationError("execution action requires an original invocation scope")
            return
        decision = self.execution_authorizations.authorize(
            context.handle,
            context.identity,
            action,
            resource,
            request_digest
            or effect_request_digest(
                "owa/action-permission/v1",
                {"identity": context.identity.key, "action": action, "resource": resource},
            ),
            execution_lease=context.handle._execution_lease,
        )
        if decision is not None and context.observer is not None:
            value = context.observer(decision.identity, decision.receipt)
            if inspect.isawaitable(value):
                await value

    def _verify_schedule_authority(
        self, connection: StorageConnection, record: ScheduleRecord
    ) -> None:
        self.execution_authorizations.reserved_schedule(record, connection=connection)

    def _reserve_scheduled_authority(
        self,
        connection: StorageConnection,
        record: ScheduleRecord,
        handle: ExecutionHandle,
        claim: ScheduleClaim,
    ) -> None:
        self.execution_authorizations.reserve_scheduled_invocation(
            handle, record, claim, connection=connection
        )

    async def authorize_action_for(
        self,
        handle: ExecutionHandle,
        identity: EffectIdentity,
        action: str,
        resource: str,
        *,
        observer: Callable[..., Any] | None = None,
    ) -> None:
        with action_scope(ExecutionActionContext(handle, identity, observer)):
            await self.authorize_current_action(action, resource)

    async def _authorize_external_lookup(
        self, identity: EffectIdentity, endpoint: str, lease: ExecutionLease | None
    ) -> None:
        handle = self.invocations.get(identity.invocation_id)
        if handle is None or handle.workflow_fingerprint != identity.workflow_fingerprint:
            raise ConfigurationError("external lookup requires the original invocation")
        self.execution_authorizations.verify_invocation(handle)
        handle._execution_lease = lease
        context = current_action_context()
        observer = (
            context.observer if context is not None and context.identity == identity else None
        )
        with action_scope(ExecutionActionContext(handle, identity, observer)):
            await self.authorize_protocol_destination("http", endpoint, {})
            helper = self.external_action_recovery
            if helper is None or helper.http is not self.protocols.http:
                raise ConfigurationError("external lookup requires its original transport")
            authentication = helper.http.authentication
            if isinstance(authentication, ProfileAuthentication):
                await self.authorize_protocol_credential(authentication.profile_name)

    async def authorize_model_completion(self) -> None:
        await self.authorize_current_action("model.complete", self.config.model.name)

    async def authorize_model_credential(self, reference: str) -> None:
        await self.authorize_current_action("credential.resolve", "model-secret:" + reference)

    async def authorize_tool_invocation(self, name: str, _payload: Any) -> None:
        await self.authorize_current_action("tool.invoke", name)

    async def preflight_agent_tool(self, name: str, payload: Any) -> None:
        self.validate_agent_tool_payload(name, payload)
        await self.authorize_tool_invocation(name, payload)
        if name == "search_knowledge":
            await self.authorize_current_action("knowledge.search", "knowledge")
        elif name in {"add_memory", "search_memory", "delete_memory"}:
            self._memory_namespace()
            action = {
                "add_memory": "memory.add",
                "search_memory": "memory.search",
                "delete_memory": "memory.delete",
            }[name]
            await self.authorize_current_action(action, "memory:self")
        else:
            await self.tools.preflight_policy(name, payload)

    async def authorize_protocol_destination(
        self, protocol: str, endpoint: str, _payload: Any
    ) -> None:
        await self.authorize_current_action("protocol.call", protocol + ":" + endpoint)
        if protocol == "mcp" and endpoint.startswith("stdio:"):
            binding_id = endpoint.removeprefix("stdio:")
            binding = next(
                (
                    server
                    for server in self.config.sandbox.mcp_stdio.servers
                    if server.id == binding_id
                ),
                None,
            )
            if binding is None:
                raise ConfigurationError("stdio credential binding is not deployment-bound")
            for reference in sorted(set(binding.secret_environment.values())):
                await self.authorize_current_action(
                    "credential.resolve", "sandbox-secret:" + reference
                )

    async def authorize_protocol_credential(self, profile_name: str) -> None:
        await self.authorize_current_action(
            "credential.resolve", "security-profile:" + profile_name
        )

    def _memory_namespace(self) -> str | None:
        context = current_action_context()
        authority = (
            self.execution_authorizations.verify_invocation(context.handle)
            if context is not None
            else None
        )
        if authority is None:
            if self.config.execution_authorization.enabled or self._bounded_memory:
                raise ConfigurationError("shared memory is unavailable without original authority")
            return None
        return (
            "owa/principal-memory/v1:"
            + hashlib.sha256(authority.principal.identity.encode("utf-8")).hexdigest()
        )

    async def _search_knowledge(self, payload: Any, _context: Any) -> Any:
        await self.authorize_current_action("knowledge.search", "knowledge")
        query = payload.get("query", "") if isinstance(payload, dict) else str(payload)
        limit = int(payload.get("limit", 5)) if isinstance(payload, dict) else 5
        return self.knowledge.search(query, limit)

    async def _add_memory(self, payload: Any, _context: Any) -> dict[str, int]:
        if isinstance(self.memory, BoundedMemoryService):
            self.memory.validate_payload("add_memory", payload)
        namespace = self._memory_namespace()
        if (
            namespace is not None
            and isinstance(payload, dict)
            and set(payload) - {"text", "metadata"}
        ):
            raise ConfigurationError("memory input cannot select another namespace")
        await self.authorize_current_action("memory.add", "memory:self")
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise ValueError("add_memory requires a text string")
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("add_memory metadata must be an object")
        if isinstance(self.memory, BoundedMemoryService):
            context = current_action_context()
            return {
                "id": self.memory.add(
                    payload["text"],
                    metadata,
                    namespace=namespace,
                    execution_handle=context.handle if context is not None else None,
                )
            }
        return {"id": self.memory.add(payload["text"], metadata, namespace=namespace)}

    async def _search_memory(self, payload: Any, _context: Any) -> list[dict[str, Any]]:
        if isinstance(self.memory, BoundedMemoryService):
            self.memory.validate_payload("search_memory", payload)
        namespace = self._memory_namespace()
        if (
            namespace is not None
            and isinstance(payload, dict)
            and set(payload) - {"query", "limit"}
        ):
            raise ConfigurationError("memory input cannot select another namespace")
        await self.authorize_current_action("memory.search", "memory:self")
        if not isinstance(payload, dict):
            payload = {"query": str(payload)}
        query = payload.get("query", "")
        limit = (
            payload.get("limit", 10)
            if isinstance(self.memory, BoundedMemoryService)
            else int(payload.get("limit", 10))
        )
        if not isinstance(query, str) or limit < 1 or limit > 100:
            raise ValueError("search_memory requires a query and a limit between 1 and 100")
        if isinstance(self.memory, BoundedMemoryService):
            context = current_action_context()
            return self.memory.search(
                query,
                limit,
                namespace=namespace,
                execution_handle=context.handle if context is not None else None,
            )
        return self.memory.search(query, limit, namespace=namespace)

    async def _delete_memory(self, payload: Any, _context: Any) -> dict[str, bool]:
        if isinstance(self.memory, BoundedMemoryService):
            self.memory.validate_payload("delete_memory", payload)
        namespace = self._memory_namespace()
        if namespace is not None and isinstance(payload, dict) and set(payload) - {"id"}:
            raise ConfigurationError("memory input cannot select another namespace")
        await self.authorize_current_action("memory.delete", "memory:self")
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), int):
            raise ValueError("delete_memory requires an integer id")
        if isinstance(self.memory, BoundedMemoryService):
            context = current_action_context()
            return {
                "deleted": self.memory.delete(
                    payload["id"],
                    namespace=namespace,
                    execution_handle=context.handle if context is not None else None,
                )
            }
        return {"deleted": self.memory.delete(payload["id"], namespace=namespace)}

    async def _prune_memory(self) -> int:
        """Owner-fenced private retention maintenance; not an agent tool."""
        if not isinstance(self.memory, BoundedMemoryService):
            raise ConfigurationError("bounded memory maintenance is not enabled")
        namespace = self._memory_namespace()
        await self.authorize_current_action("memory.prune", "memory:self")
        context = current_action_context()
        return self.memory.prune_due(
            namespace=namespace, execution_handle=context.handle if context is not None else None
        )

    async def invoke_agent_tool(self, name: str, payload: Any) -> Any:
        if name not in self.agent_tools:
            raise ConfigurationError("agent tool is not deployment-bound")
        await self.authorize_tool_invocation(name, payload)
        if name == "search_knowledge":
            return await self._search_knowledge(payload, None)
        if name == "add_memory":
            return await self._add_memory(payload, None)
        if name == "search_memory":
            return await self._search_memory(payload, None)
        if name == "delete_memory":
            return await self._delete_memory(payload, None)
        return await self.tools.invoke(name, payload)

    def validate_agent_tool_payload(self, name: str, payload: Any) -> None:
        """Validate a whole role tool batch before any member can act."""
        if name not in self.agent_tools:
            raise ConfigurationError("agent tool is not deployment-bound")
        if name in {"add_memory", "search_memory", "delete_memory"} and isinstance(
            self.memory, BoundedMemoryService
        ):
            self.memory.validate_payload(name, payload)
            return
        if name not in {"search_knowledge", "add_memory", "search_memory", "delete_memory"}:
            self.tools.validate_payload(name, payload, strict=True)
            return
        if not isinstance(payload, dict):
            if name in {"search_knowledge", "search_memory"}:
                return
            raise ConfigurationError("memory tool requires a bounded object")
        fields = {
            "search_knowledge": {"query", "limit"},
            "add_memory": {"text", "metadata"},
            "search_memory": {"query", "limit"},
            "delete_memory": {"id"},
        }[name]
        if set(payload) - fields:
            raise ConfigurationError("role memory/knowledge input cannot choose its namespace")
        if name == "add_memory" and (
            type(payload.get("text")) is not str or type(payload.get("metadata", {})) is not dict
        ):
            raise ConfigurationError("memory tool input is invalid")
        if name == "delete_memory" and type(payload.get("id")) is not int:
            raise ConfigurationError("memory tool input is invalid")
        if name in {"search_knowledge", "search_memory"} and (
            type(payload.get("query", "")) is not str
            or type(payload.get("limit", 5)) is not int
            or not 1 <= payload.get("limit", 5) <= 100
        ):
            raise ConfigurationError("search tool input is invalid")

    @property
    def role_function_calls(self) -> frozenset[str]:
        self.role_bindings.verify()
        return self.role_bindings.function_calls

    def agent_tool_bindings(self) -> tuple[AgentToolBinding, ...]:
        bindings: list[AgentToolBinding] = [
            AgentToolBinding(
                name="search_knowledge",
                description="Search mounted knowledge documents.",
                invoke=lambda payload: self.invoke_agent_tool("search_knowledge", payload),
            )
        ]
        if self.memory_enabled:
            bindings.extend(
                [
                    AgentToolBinding(
                        name="add_memory",
                        description="Store durable agent memory with optional metadata.",
                        invoke=lambda payload: self.invoke_agent_tool("add_memory", payload),
                    ),
                    AgentToolBinding(
                        name="search_memory",
                        description="Search long-term agent memory.",
                        invoke=lambda payload: self.invoke_agent_tool("search_memory", payload),
                    ),
                    AgentToolBinding(
                        name="delete_memory",
                        description="Delete a long-term agent memory by id.",
                        invoke=lambda payload: self.invoke_agent_tool("delete_memory", payload),
                    ),
                ]
            )
        bindings.extend(self.tools.bindings())
        return tuple(bindings)

    async def call_protocol(
        self,
        protocol: str,
        payload: Any,
        *,
        _a2a_selection: A2ACardSelection | None = None,
        _openapi_selection: OpenApiOperationSelection | None = None,
    ) -> Any:
        self.verify_protocol_authorization_binding()
        prepared = workflow_protocol_payload(protocol, payload)
        if _a2a_selection is None and _openapi_selection is None:
            return await self.protocols.call(protocol, prepared)
        return await self.protocols.call(
            protocol,
            prepared,
            _a2a_selection=_a2a_selection,
            _openapi_selection=_openapi_selection,
        )

    def verify_protocol_authorization_binding(self) -> None:
        if self.config.execution_authorization.enabled and (
            self.protocols.authorize_destination != self.authorize_protocol_destination
            or self.protocols.authorize_credential != self.authorize_protocol_credential
        ):
            raise ConfigurationError("protocol authorization is not deployment-bound")

    def close(self) -> None:
        if self._approval_expiry_dispatcher is not None:
            self._approval_expiry_dispatcher.close()
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
