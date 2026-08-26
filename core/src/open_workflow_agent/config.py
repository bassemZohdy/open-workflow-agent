"""Strict runtime configuration and environment overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

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


class CatalogAuthenticationConfig(StrictModel):
    """Deployment-owned references to catalog credentials.

    Workflow documents are intentionally not allowed to carry credentials. The
    resolver reads these environment variables only when it talks to a
    deployment-trusted catalog or invokes a function loaded from one.
    """

    bearer_token_env: str | None = None
    basic_username_env: str | None = None
    basic_password_env: str | None = None

    @model_validator(mode="after")
    def validate_basic_pair(self) -> CatalogAuthenticationConfig:
        if bool(self.basic_username_env) != bool(self.basic_password_env):
            raise ValueError(
                "basic_username_env and basic_password_env must be configured together"
            )
        return self


class ExternalCatalogConfig(StrictModel):
    """A deployment-controlled trust policy for one workflow catalog alias."""

    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_endpoints: list[str] = Field(default_factory=list)
    timeout_seconds: float = 10.0
    max_response_bytes: int = 4_000_000
    follow_redirects: bool = False
    verify_tls: bool = True
    cache_ttl_seconds: float = 300.0
    max_cache_age_seconds: float = 86_400.0
    max_cache_entries: int = 128
    revalidate: bool = True
    integrity_pins: dict[str, str] = Field(default_factory=dict)
    require_integrity_pin: bool = False
    authentication: CatalogAuthenticationConfig = Field(default_factory=CatalogAuthenticationConfig)

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, value: list[str]) -> list[str]:
        hosts = [host.strip().lower() for host in value]
        if any(not host for host in hosts):
            raise ValueError("allowed_hosts cannot contain empty values")
        if len(set(hosts)) != len(hosts):
            raise ValueError("allowed_hosts must not contain duplicates")
        return hosts

    @field_validator("allowed_endpoints")
    @classmethod
    def validate_allowed_endpoints(cls, value: list[str]) -> list[str]:
        endpoints = [endpoint.strip().rstrip("/") for endpoint in value]
        if any(not endpoint for endpoint in endpoints):
            raise ValueError("allowed_endpoints cannot contain empty values")
        if len(set(endpoints)) != len(endpoints):
            raise ValueError("allowed_endpoints must not contain duplicates")
        for endpoint in endpoints:
            parsed = urlparse(endpoint)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("allowed_endpoints must contain absolute HTTPS URLs")
            if parsed.username or parsed.password:
                raise ValueError("allowed_endpoints cannot contain credentials")
        return endpoints

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        return value

    @field_validator("max_response_bytes")
    @classmethod
    def validate_response_limit(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_response_bytes must be greater than zero")
        return value

    @field_validator("cache_ttl_seconds")
    @classmethod
    def validate_cache_ttl(cls, value: float) -> float:
        if value < 0:
            raise ValueError("cache_ttl_seconds cannot be negative")
        return value

    @field_validator("max_cache_age_seconds")
    @classmethod
    def validate_cache_age(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("max_cache_age_seconds must be greater than zero")
        return value

    @field_validator("max_cache_entries")
    @classmethod
    def validate_cache_entries(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_cache_entries must be greater than zero")
        return value

    @field_validator("integrity_pins")
    @classmethod
    def validate_integrity_pins(cls, value: dict[str, str]) -> dict[str, str]:
        for key, digest in value.items():
            if not key.strip():
                raise ValueError("integrity pin keys cannot be empty")
            if len(digest) != 64 or any(
                character not in "0123456789abcdefABCDEF" for character in digest
            ):
                raise ValueError("integrity pins must be SHA-256 hex digests")
        return {key.strip(): digest.lower() for key, digest in value.items()}

    @model_validator(mode="after")
    def validate_secure_transport(self) -> ExternalCatalogConfig:
        if self.follow_redirects:
            raise ValueError("external catalog redirects must remain disabled")
        if not self.verify_tls:
            raise ValueError("external catalog TLS verification cannot be disabled")
        return self


class WorkflowConfig(StrictModel):
    path: str | None = None
    definition: dict[str, Any] | None = None
    catalog: list[dict[str, Any]] = Field(default_factory=list)
    external_catalogs: dict[str, ExternalCatalogConfig] = Field(default_factory=dict)


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


class ApprovalConfig(StrictModel):
    enabled: bool = False
    operator_token: str | None = None


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
    approvals: ApprovalConfig = Field(default_factory=ApprovalConfig)
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
