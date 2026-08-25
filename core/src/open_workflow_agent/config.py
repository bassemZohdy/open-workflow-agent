"""Strict runtime configuration and environment overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import ConfigurationError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ModelConfig(StrictModel):
    provider: str = "litellm"
    name: str = "fake/default"
    temperature: float = 0.0
    options: dict[str, Any] = Field(default_factory=dict)


class AgentConfig(StrictModel):
    name: str = "default"
    instruction: str = "You are a helpful assistant."
    tools: list[str] = Field(default_factory=list)


class WorkflowConfig(StrictModel):
    path: str | None = None
    definition: dict[str, Any] | None = None


class ReloadConfig(StrictModel):
    mode: Literal["startup", "manual", "watch"] = "startup"
    interval_seconds: float = 30.0


class KnowledgeConfig(StrictModel):
    path: str = "/knowledge"
    database: str = "/data/knowledge.sqlite3"
    reload: ReloadConfig = Field(default_factory=ReloadConfig)
    chunk_size: int = 400
    chunk_overlap: int = 40


class EmbeddingConfig(StrictModel):
    # sentence-transformers remains accepted as a migration alias for existing
    # config files; the packaged implementation is FastEmbed/ONNX.
    provider: Literal["fastembed", "sentence-transformers"] = "fastembed"
    model: str = "sentence-transformers/all-MiniLM-L6-v2"
    revision: str = "ea78891063587eb050ed4166b20062eaf978037c"


class MemoryConfig(StrictModel):
    enabled: bool | Literal["auto"] = "auto"
    database: str = "/data/memory.sqlite3"


class PersistenceConfig(StrictModel):
    datasource: str | None = None
    database: str = "/data/runtime.sqlite3"


class ToolConfig(StrictModel):
    type: Literal["mcp", "openapi", "a2a"]
    name: str | None = None
    endpoint: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class ServerConfig(StrictModel):
    host: str = "0.0.0.0"
    port: int = 8080
    max_request_bytes: int = 1_048_576


class ObservabilityConfig(StrictModel):
    log_level: str = "INFO"


class RuntimeConfig(StrictModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    persistence: PersistenceConfig = Field(default_factory=PersistenceConfig)
    tools: list[ToolConfig] = Field(default_factory=list)
    server: ServerConfig = Field(default_factory=ServerConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> RuntimeConfig:
        selected = (
            Path(path)
            if path is not None
            else Path(os.getenv("OWA_CONFIG_FILE") or "/config/agent.yaml")
        )
        raw: dict[str, Any] = {}
        if selected.exists():
            try:
                loaded = yaml.safe_load(selected.read_text(encoding="utf-8")) or {}
                if not isinstance(loaded, dict):
                    raise ConfigurationError("configuration root must be an object")
                raw = loaded
            except yaml.YAMLError as exc:
                raise ConfigurationError(f"invalid YAML configuration: {exc}") from exc
        _apply_environment(raw)
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise ConfigurationError(
                "invalid runtime configuration", details={"errors": exc.errors()}
            ) from exc


def _apply_environment(target: dict[str, Any]) -> None:
    """Apply OWA__A__B environment variables over YAML values."""

    for key, value in os.environ.items():
        if not key.startswith("OWA__") or key == "OWA_CONFIG_FILE":
            continue
        parts = [part.lower() for part in key[5:].split("__") if part]
        if not parts:
            continue
        cursor = target
        for part in parts[:-1]:
            existing = cursor.get(part)
            if not isinstance(existing, dict):
                existing = {}
                cursor[part] = existing
            cursor = existing
        cursor[parts[-1]] = _parse_env_value(value)


def _parse_env_value(value: str) -> Any:
    try:
        return yaml.safe_load(value)
    except yaml.YAMLError:
        return value
