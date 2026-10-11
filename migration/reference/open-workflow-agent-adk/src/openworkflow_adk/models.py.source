"""Pydantic models for the OpenWorkflow document envelope and task list."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROVIDER_TYPES = {"gemini", "anthropic", "openai", "bedrock", "azure", "ollama", "vllm"}
MEMORY_TYPES = {"in-memory", "file", "redis", "postgres", "vertex"}


class ProviderReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use: str


class ProviderConfig(BaseModel):
    """Named model-provider connection configuration."""

    model_config = ConfigDict(extra="allow")

    type: str
    endpoint: str | None = None
    base_url: str | None = None
    credential: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def known_type(cls, value: str) -> str:
        if value not in PROVIDER_TYPES:
            raise ValueError(f"unknown provider type {value!r}")
        return value


class MemoryConfig(BaseModel):
    """Named semantic-memory backend configuration."""

    model_config = ConfigDict(extra="allow")

    type: str
    connection: str | None = None
    namespace: str | None = None
    index: str | None = None
    retention: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def known_type(cls, value: str) -> str:
        if value not in MEMORY_TYPES:
            raise ValueError(f"unknown memory type {value!r}")
        return value


class CatalogConfig(BaseModel):
    """Reusable catalog endpoint retained for interoperability compatibility."""

    model_config = ConfigDict(extra="allow")

    endpoint: Any = None


class ModelReference(BaseModel):
    """Reference to a named model bundle in ``document.metadata.adk.models``."""

    model_config = ConfigDict(extra="forbid")

    use: str


class ModelSpec(BaseModel):
    """Reusable model configuration shared by agent tasks."""

    model_config = ConfigDict(extra="forbid")

    model: str
    provider: ProviderReference | None = None
    generate_content_config: dict[str, Any] | None = None
    output_schema: Any = None
    display_name: str | None = None
    description: str | None = None


class AgentCharacteristics(BaseModel):
    """Project extension describing how a task is executed by ADK."""

    model_config = ConfigDict(extra="forbid")

    model: str | ModelReference | None = None
    name: str | None = None
    description: str | None = None
    instruction: str | None = None
    tools: list[Any] = Field(default_factory=list)
    generate_content_config: dict[str, Any] | None = None
    output_key: str | None = None
    memory: ProviderReference | None = None
    provider: ProviderReference | None = None
    sub_agents: list[AgentCharacteristics] = Field(default_factory=list)
    request_input: dict[str, Any] | None = None


class TaskAdkMetadata(BaseModel):
    """ADK task-level configuration allowed inside ``task.metadata.adk``."""

    model_config = ConfigDict(extra="forbid")

    agent: AgentCharacteristics | None = None
    self_heal: dict[str, Any] | None = None


class DocumentAdkMetadata(BaseModel):
    """ADK document-level configuration allowed inside ``document.metadata.adk``."""

    model_config = ConfigDict(extra="forbid")

    agent_defaults: AgentCharacteristics | None = None
    models: dict[str, ModelSpec] | None = None
    providers: dict[str, ProviderConfig] | None = None
    memories: dict[str, MemoryConfig] | None = None


AdkMetadata = TaskAdkMetadata | DocumentAdkMetadata
"""Backward-compatible type alias for ADK metadata at either location."""


class WorkflowMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dsl: str
    namespace: str
    name: str
    version: str
    title: str | None = None
    summary: str | None = None
    tags: dict[str, Any] = Field(default_factory=dict)
    metadata: WorkflowMetadata | None = None


class UseDefinition(BaseModel):
    """Typed reusable component registries from the workflow document."""

    model_config = ConfigDict(extra="forbid")

    authentications: dict[str, Any] = Field(default_factory=dict)
    errors: dict[str, Any] = Field(default_factory=dict)
    extensions: list[dict[str, Any]] = Field(default_factory=list)
    functions: dict[str, Any] = Field(default_factory=dict)
    retries: dict[str, Any] = Field(default_factory=dict)
    secrets: list[str] = Field(default_factory=list)
    timeouts: dict[str, Any] = Field(default_factory=dict)
    catalogs: dict[str, CatalogConfig] = Field(default_factory=dict)


class TaskBase(BaseModel):
    """Fields common to every OpenWorkflow task."""

    model_config = ConfigDict(extra="allow")

    if_: str | None = Field(default=None, alias="if")
    input: Any = None
    output: Any = None
    export: Any = None
    timeout: Any = None
    then: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def adk_metadata(self) -> TaskAdkMetadata | None:
        """Return validated ADK metadata if the task carries it."""
        payload = self.metadata.get("adk")
        if isinstance(payload, dict):
            return TaskAdkMetadata.model_validate(payload)
        return None

    def effective_agent(self) -> AgentCharacteristics | None:
        """ADK agent config read from ``metadata.adk.agent``."""
        adk = self.adk_metadata()
        return adk.agent if adk else None

    def effective_self_heal(self) -> dict[str, Any] | None:
        """Self-heal config read from ``metadata.adk.self_heal``."""
        adk = self.adk_metadata()
        return adk.self_heal if adk else None


TASK_KEYS = (
    "call",
    "do",
    "fork",
    "emit",
    "for",
    "listen",
    "raise",
    "run",
    "set",
    "switch",
    "try",
    "wait",
)


class Task(TaskBase):
    """A typed task envelope retaining the schema-defined task configuration."""

    call: str | None = None
    do: list[TaskItem] | None = None
    fork: dict[str, Any] | None = None
    emit: dict[str, Any] | None = None
    for_: dict[str, Any] | None = Field(default=None, alias="for")
    listen: dict[str, Any] | None = None
    raise_: dict[str, Any] | None = Field(default=None, alias="raise")
    run: dict[str, Any] | None = None
    set: dict[str, Any] | None = None
    switch: list[dict[str, Any]] | None = None
    try_: list[TaskItem] | None = Field(default=None, alias="try")
    wait: Any = None
    with_: dict[str, Any] | None = Field(default=None, alias="with")
    catch: dict[str, Any] | None = None
    while_: dict[str, Any] | None = Field(default=None, alias="while")

    @model_validator(mode="after")
    def has_one_task_kind(self) -> Task:
        # `for` and `do` are both present on the schema's loop task; together
        # they represent one kind rather than two competing discriminators.
        do_is_loop_body = self.for_ is not None and self.do is not None
        present = [
            key
            for key in TASK_KEYS
            if not (key == "do" and do_is_loop_body)
            if getattr(self, key if key not in {"for", "raise", "try"} else f"{key}_") is not None
        ]
        if len(present) != 1:
            raise ValueError(f"task must contain exactly one task kind, found {present or 'none'}")
        return self


class TaskItem(BaseModel):
    """Named task entry as represented in a `do` list."""

    name: str
    task: Task

    @model_validator(mode="before")
    @classmethod
    def from_named_mapping(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and "name" not in value
            and "task" not in value
            and len(value) == 1
        ):
            name, task = next(iter(value.items()))
            return {"name": name, "task": task}
        return value


class OpenWorkflowDocument(BaseModel):
    """Parsed OpenWorkflow document with project extensions preserved."""

    model_config = ConfigDict(extra="forbid")

    document: WorkflowDefinition
    do: list[TaskItem]
    input: Any = None
    use: UseDefinition = Field(default_factory=UseDefinition)
    timeout: Any = None
    output: Any = None
    schedule: dict[str, Any] | None = None

    def adk_metadata(self) -> DocumentAdkMetadata | None:
        """Return validated ADK metadata from document.metadata if present."""
        if self.document.metadata is None:
            return None
        payload = (self.document.metadata.model_extra or {}).get("adk")
        if isinstance(payload, dict):
            return DocumentAdkMetadata.model_validate(payload)
        return None

    def effective_models(self) -> dict[str, ModelSpec]:
        """Model registry read from ``document.metadata.adk.models``."""
        adk = self.adk_metadata()
        return adk.models if adk else {}

    def effective_providers(self) -> dict[str, ProviderConfig]:
        """Provider registry read from ``document.metadata.adk.providers``."""
        adk = self.adk_metadata()
        return adk.providers if adk else {}

    def effective_memories(self) -> dict[str, MemoryConfig]:
        """Memory registry read from ``document.metadata.adk.memories``."""
        adk = self.adk_metadata()
        return adk.memories if adk else {}

    def effective_agent_defaults(self) -> AgentCharacteristics | None:
        """Default agent characteristics read from ``document.metadata.adk.agent_defaults``."""
        adk = self.adk_metadata()
        return adk.agent_defaults if adk else None
