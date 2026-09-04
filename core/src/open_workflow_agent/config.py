"""Strict runtime configuration and environment overrides."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ._version import __version__
from .errors import ConfigurationError
from .security import AuthorizationPolicy, SecurityConfig

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IMAGE_DIGEST = re.compile(r"^.+@sha256:[0-9a-fA-F]{64}$")


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
    resolver resolves the named security profile only when it talks to a
    deployment-trusted catalog or invokes a function loaded from one.
    """

    security_profile: str | None = None


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


_A2A_SKILL_ID = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


class A2ASkillConfig(StrictModel):
    """Deployment-declared A2A skill mapped to an explicitly registered workflow.

    Clients never select workflow paths, files, or catalog entries; routing is
    deployment-owned through this declaration.
    """

    id: str
    workflow: str
    name: str | None = None
    description: str = "Executes the mapped deployment-configured workflow."
    tags: list[str] = Field(default_factory=lambda: ["workflow"])

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        selected = value.strip()
        if not selected or len(selected) > 63 or not _A2A_SKILL_ID.fullmatch(selected):
            raise ValueError("a2a skill id must be a lowercase DNS-like identifier")
        return selected

    @field_validator("workflow")
    @classmethod
    def validate_workflow(cls, value: str) -> str:
        selected = value.strip()
        if not selected or len(selected) > 128:
            raise ValueError("a2a skill workflow must be a registered workflow name")
        return selected

    @field_validator("name", "description")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a2a skill name/description must not be empty")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        selected = [tag.strip() for tag in value]
        if any(not tag for tag in selected) or len(selected) != len(set(selected)):
            raise ValueError("a2a skill tags must be unique non-empty values")
        return selected


class A2AConfig(StrictModel):
    """Deployment policy for inbound A2A exposure (bounded profile)."""

    enabled: bool = False
    transport: Literal["jsonrpc", "http_json"] = "jsonrpc"
    path: str = "/a2a"
    agent_name: str = "Open Workflow Agent"
    agent_description: str = "Configuration-driven Open Workflow runtime over A2A."
    agent_version: str = __version__
    public_base_url: str | None = None
    security_profile: str | None = None
    authorization: AuthorizationPolicy | None = None
    skills: tuple[A2ASkillConfig, ...] = Field(default_factory=tuple)
    max_message_chars: int = Field(default=100_000, gt=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        selected = value.strip()
        if not selected.startswith("/") or len(selected) > 128:
            raise ValueError("a2a path must start with '/' and stay under 128 characters")
        return selected.rstrip("/")

    @field_validator("skills")
    @classmethod
    def validate_unique_skill_ids(
        cls, value: tuple[A2ASkillConfig, ...]
    ) -> tuple[A2ASkillConfig, ...]:
        ids = [skill.id for skill in value]
        if len(ids) != len(set(ids)):
            raise ValueError("a2a skill ids must be unique")
        return value

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        selected = value.strip().rstrip("/")
        if not selected.startswith(("http://", "https://")) or "://" in selected[7:]:
            raise ValueError("a2a public_base_url must be an absolute http(s) URL")
        return selected


class ApprovalConfig(StrictModel):
    enabled: bool = False
    operator_security_profile: str | None = None


class DockerSandboxConfig(StrictModel):
    """Deployment-owned policy for the restricted Docker sandbox controller."""

    controller_socket: str = "/run/owa-sandbox/controller.sock"
    allowed_images: list[str] = Field(default_factory=list)
    require_digest: bool = True
    run_as_user: str = "65532:65532"
    network: Literal["denied"] = "denied"

    @field_validator("controller_socket")
    @classmethod
    def validate_controller_socket(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("sandbox docker controller_socket must be an absolute path")
        return str(path)

    @field_validator("allowed_images")
    @classmethod
    def validate_allowed_images(cls, value: list[str]) -> list[str]:
        images = [image.strip() for image in value]
        if any(not image for image in images):
            raise ValueError("sandbox docker allowed_images cannot contain empty values")
        if len(set(images)) != len(images):
            raise ValueError("sandbox docker allowed_images must not contain duplicates")
        return images

    @field_validator("run_as_user")
    @classmethod
    def validate_run_as_user(cls, value: str) -> str:
        user = value.strip()
        if not re.fullmatch(r"[1-9][0-9]*(?::[0-9]+)?", user):
            raise ValueError("sandbox docker run_as_user must be a non-root numeric uid[:gid]")
        return user

    @model_validator(mode="after")
    def validate_digest_policy(self) -> DockerSandboxConfig:
        if self.require_digest:
            invalid = [image for image in self.allowed_images if not _IMAGE_DIGEST.fullmatch(image)]
            if invalid:
                raise ValueError(
                    "sandbox docker allowed_images must use immutable sha256 digests when "
                    "require_digest=true"
                )
        return self


class KubernetesSandboxConfig(StrictModel):
    """Deployment policy for a restricted Kubernetes/OpenShift controller sidecar."""

    controller_url: str = "http://127.0.0.1:8090"
    allowed_images: list[str] = Field(default_factory=list)
    require_digest: bool = True
    platform: Literal["kubernetes", "openshift"] = "kubernetes"
    network: Literal["denied"] = "denied"
    network_policy_enforced: bool = False
    process_limit_enforced: bool = False
    secret_name: str | None = None
    secret_keys: list[str] = Field(default_factory=list)

    @field_validator("controller_url")
    @classmethod
    def validate_controller_url(cls, value: str) -> str:
        selected = value.strip().rstrip("/")
        parsed = urlparse(selected)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError(
                "sandbox kubernetes controller_url must use a loopback HTTP(S) endpoint"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "sandbox kubernetes controller_url cannot contain credentials or query data"
            )
        return selected

    @field_validator("allowed_images")
    @classmethod
    def validate_allowed_images(cls, value: list[str]) -> list[str]:
        images = [image.strip() for image in value]
        if any(not image for image in images):
            raise ValueError("sandbox kubernetes allowed_images cannot contain empty values")
        if len(set(images)) != len(images):
            raise ValueError("sandbox kubernetes allowed_images must not contain duplicates")
        return images

    @field_validator("secret_keys")
    @classmethod
    def validate_secret_keys(cls, value: list[str]) -> list[str]:
        keys = [key.strip() for key in value]
        if any(not _ENVIRONMENT_NAME.fullmatch(key) for key in keys):
            raise ValueError("sandbox kubernetes secret_keys must be valid environment-style names")
        if len(set(keys)) != len(keys):
            raise ValueError("sandbox kubernetes secret_keys must not contain duplicates")
        return keys

    @model_validator(mode="after")
    def validate_policy(self) -> KubernetesSandboxConfig:
        if self.require_digest:
            invalid = [image for image in self.allowed_images if not _IMAGE_DIGEST.fullmatch(image)]
            if invalid:
                raise ValueError(
                    "sandbox kubernetes allowed_images must use immutable sha256 digests when "
                    "require_digest=true"
                )
        if self.secret_keys and not self.secret_name:
            raise ValueError("sandbox kubernetes secret_name is required when secret_keys are set")
        return self


class SandboxConfig(StrictModel):
    """Deployment policy for the selected framework-neutral sandbox backend."""

    enabled: bool = False
    backend: Literal["internal", "docker", "kubernetes"] = "internal"
    allow_shell: bool = False
    script_runtimes: list[str] = Field(default_factory=lambda: ["python"])
    timeout_seconds: float = 30.0
    max_input_bytes: int = 1_048_576
    max_output_bytes: int = 1_048_576
    max_workspace_bytes: int = 33_554_432
    workspace_root: str = "/tmp/owa-sandbox"
    executable_search_path: str = "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
    inherited_environment: list[str] = Field(default_factory=list)
    secret_environment: list[str] = Field(default_factory=list)
    cpu_seconds: int | None = 30
    memory_bytes: int | None = 536_870_912
    file_size_bytes: int | None = 33_554_432
    process_count: int | None = 64
    docker: DockerSandboxConfig = Field(default_factory=DockerSandboxConfig)
    kubernetes: KubernetesSandboxConfig = Field(default_factory=KubernetesSandboxConfig)

    @field_validator("script_runtimes")
    @classmethod
    def validate_script_runtimes(cls, value: list[str]) -> list[str]:
        runtimes = [runtime.strip().lower() for runtime in value]
        if not runtimes or any(not runtime for runtime in runtimes):
            raise ValueError("script_runtimes must contain at least one runtime")
        if len(set(runtimes)) != len(runtimes):
            raise ValueError("script_runtimes must not contain duplicates")
        unsupported = sorted(set(runtimes) - {"python"})
        if unsupported:
            raise ValueError(f"unsupported internal script runtimes: {unsupported}")
        return runtimes

    @field_validator("timeout_seconds")
    @classmethod
    def validate_sandbox_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("sandbox timeout_seconds must be greater than zero")
        return value

    @field_validator(
        "max_input_bytes",
        "max_output_bytes",
        "max_workspace_bytes",
    )
    @classmethod
    def validate_positive_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("sandbox byte limits must be greater than zero")
        return value

    @field_validator("cpu_seconds", "memory_bytes", "file_size_bytes", "process_count")
    @classmethod
    def validate_optional_limits(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("sandbox resource limits must be greater than zero when configured")
        return value

    @field_validator("workspace_root")
    @classmethod
    def validate_workspace_root(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("sandbox workspace_root must be an absolute path")
        return str(path)

    @field_validator("executable_search_path")
    @classmethod
    def validate_executable_search_path(cls, value: str) -> str:
        paths = [item for item in value.split(os.pathsep) if item]
        if not paths or any(not Path(item).is_absolute() for item in paths):
            raise ValueError("sandbox executable_search_path must contain absolute paths")
        return os.pathsep.join(paths)

    @field_validator("inherited_environment", "secret_environment")
    @classmethod
    def validate_environment_names(cls, value: list[str]) -> list[str]:
        names = [name.strip() for name in value]
        if any(not _ENVIRONMENT_NAME.fullmatch(name) for name in names):
            raise ValueError("sandbox environment names must be valid environment variable names")
        if len(set(names)) != len(names):
            raise ValueError("sandbox environment lists must not contain duplicates")
        return names

    @model_validator(mode="after")
    def validate_environment_boundaries(self) -> SandboxConfig:
        overlap = sorted(set(self.inherited_environment) & set(self.secret_environment))
        if overlap:
            raise ValueError(
                f"sandbox inherited_environment and secret_environment must not overlap: {overlap}"
            )
        if self.enabled and self.backend == "docker" and not self.docker.allowed_images:
            raise ValueError(
                "enabled Docker sandbox requires at least one deployment-approved image"
            )
        if self.enabled and self.backend == "kubernetes":
            if not self.kubernetes.allowed_images:
                raise ValueError(
                    "enabled Kubernetes sandbox requires at least one deployment-approved image"
                )
            if self.kubernetes.network == "denied" and not self.kubernetes.network_policy_enforced:
                raise ValueError(
                    "enabled Kubernetes sandbox requires network_policy_enforced=true "
                    "for denied networking"
                )
            if self.process_count is not None and not self.kubernetes.process_limit_enforced:
                raise ValueError(
                    "enabled Kubernetes sandbox requires process_limit_enforced=true "
                    "when process_count is set"
                )
        return self


class ToolConfig(StrictModel):
    type: Literal["mcp", "openapi", "a2a"]
    name: str | None = None
    endpoint: str | None = None
    security_profile: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class ProtocolConfig(StrictModel):
    """Deployment policy for workflow-initiated outbound protocol calls."""

    security_profile: str | None = None


class RateLimitConfig(StrictModel):
    """Deployment-controlled rate limiting policy."""

    requests_per_second: float = Field(default=10.0, gt=0)
    burst: int = Field(default=20, gt=0)


class ConcurrencyLimitConfig(StrictModel):
    """Deployment-controlled concurrency limiting policy."""

    max_concurrent: int = Field(default=50, gt=0)


class TrafficPolicyConfig(StrictModel):
    """Deployment-controlled traffic policy for rate limits, concurrency limits, and burst/admission control.

    Authentication/authorization profiles must not own traffic management.
    """

    enabled: bool = False
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    concurrency_limit: ConcurrencyLimitConfig = Field(default_factory=ConcurrencyLimitConfig)


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
    a2a: A2AConfig = Field(default_factory=A2AConfig)
    protocols: ProtocolConfig = Field(default_factory=ProtocolConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    traffic_policy: TrafficPolicyConfig = Field(default_factory=TrafficPolicyConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    tools: list[ToolConfig] = Field(default_factory=list)
    server: ServerConfig = Field(default_factory=ServerConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @model_validator(mode="after")
    def validate_a2a_security_profile(self) -> RuntimeConfig:
        name = self.a2a.security_profile
        if name is None:
            if self.a2a.authorization is not None and self.a2a.authorization.rules:
                raise ValueError(
                    "a2a.authorization requires a2a.security_profile; "
                    "authorization without authenticated principals is not supported"
                )
            return self
        if name not in self.security.profiles:
            raise ValueError(f"a2a.security_profile references unknown security profile: {name}")
        if self.security.profiles[name].type != "bearer":
            raise ValueError(
                "a2a.security_profile must reference a security profile of type 'bearer'"
            )
        return self

    @model_validator(mode="after")
    def validate_security_profile_references(self) -> RuntimeConfig:
        header_capable = {"bearer", "api_key"}

        def require_header_profile(field: str, name: str | None) -> None:
            if name is None:
                return
            profile = self.security.profiles.get(name)
            if profile is None:
                raise ValueError(f"{field} references unknown security profile: {name}")
            if profile.type not in header_capable:
                raise ValueError(
                    f"{field} must reference a security profile of type "
                    f"{' or '.join(sorted(header_capable))}"
                )

        require_header_profile(
            "approvals.operator_security_profile", self.approvals.operator_security_profile
        )
        require_header_profile("protocols.security_profile", self.protocols.security_profile)
        for catalog_name, catalog in self.workflow.external_catalogs.items():
            require_header_profile(
                f"external catalog '{catalog_name}' authentication",
                catalog.authentication.security_profile,
            )
        for index, tool in enumerate(self.tools):
            require_header_profile(f"tools[{index}]", tool.security_profile)
        return self

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
