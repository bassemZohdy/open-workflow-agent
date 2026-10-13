"""Strict runtime configuration and environment overrides."""

from __future__ import annotations

import base64
import hashlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ._version import __version__
from .errors import ConfigurationError, ToolError
from .security import AuthorizationPolicy, ExecutionAuthorizationConfig, SecurityConfig

if TYPE_CHECKING:
    from .grpc_client import GrpcBinding


_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IMAGE_DIGEST = re.compile(r"^.+@sha256:[0-9a-fA-F]{64}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ModelConfig(StrictModel):
    provider: str = "fake"
    name: str = "fake/default"
    temperature: float = 0.0
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def infer_default_provider(cls, value: Any) -> Any:
        # Preserve the model-only configuration contract for real providers;
        # the packaged fake namespace is usable without external credentials.
        if isinstance(value, dict) and "provider" not in value:
            name = value.get("name", "fake/default")
            provider = "fake" if isinstance(name, str) and name.startswith("fake/") else "litellm"
            return {**value, "provider": provider}
        return value


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


class ExternalResourceConfig(ExternalCatalogConfig):
    """Exact schema URLs and mandatory digest pins; independent catalog trust."""

    require_integrity_pin: Literal[True] = True

    @field_validator("allowed_endpoints")
    @classmethod
    def validate_allowed_endpoints(cls, value: list[str]) -> list[str]:
        endpoints = [endpoint.strip() for endpoint in value]
        if len(set(endpoints)) != len(endpoints):
            raise ValueError("schema resource endpoints must be unique")
        for endpoint in endpoints:
            parsed = urlparse(endpoint)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
                or any(char.isspace() for char in endpoint)
            ):
                raise ValueError("schema resource endpoints must be exact HTTPS document URLs")
        return endpoints


class WorkflowConfig(StrictModel):
    path: str | None = None
    definition: dict[str, Any] | None = None
    catalog: list[dict[str, Any]] = Field(default_factory=list)
    external_catalogs: dict[str, ExternalCatalogConfig] = Field(default_factory=dict)
    external_resources: dict[str, ExternalResourceConfig] = Field(default_factory=dict)


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
    max_pending_tasks: int = Field(default=1000, ge=1, le=100_000)

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
        try:
            parsed = urlparse(selected)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("a2a public_base_url must be an absolute http(s) URL") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("a2a public_base_url must be an absolute http(s) URL")
        return selected


class ApprovalConfig(StrictModel):
    enabled: bool = False
    operator_security_profile: str | None = None
    protected: bool = False
    actor_security_profiles: list[str] = Field(default_factory=list, max_length=32)
    max_pending: int = Field(default=1000, ge=1, le=10000)
    max_policy_bytes: int = Field(default=65536, ge=1024, le=65536)
    poll_interval_seconds: float = Field(default=0.25, gt=0, le=60, allow_inf_nan=False)
    scan_limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_protected_profile(self) -> ApprovalConfig:
        if len(set(self.actor_security_profiles)) != len(self.actor_security_profiles) or any(
            not name or name != name.strip() for name in self.actor_security_profiles
        ):
            raise ValueError("approval actor profile references must be unique and nonempty")
        if self.protected and (not self.enabled or not self.actor_security_profiles):
            raise ValueError("protected approvals require enabled approvals and actor profiles")
        return self


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


class McpStdioServerConfig(StrictModel):
    """One exact deployment-approved executable binding; no workflow secret values."""

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    command: str
    arguments: list[str] = Field(default_factory=list, max_length=128)
    environment: dict[str, str] = Field(default_factory=dict, max_length=64)
    secret_environment: dict[str, str] = Field(default_factory=dict, max_length=64)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        if not Path(value).is_absolute() or "\x00" in value or "${" in value:
            raise ValueError("MCP stdio command must be an absolute deployment executable")
        return value

    @model_validator(mode="after")
    def validate_environment(self) -> McpStdioServerConfig:
        names = {*self.environment, *self.secret_environment}
        if any(not _ENVIRONMENT_NAME.fullmatch(name) for name in names):
            raise ValueError("MCP stdio environment names are invalid")
        if names & {"PATH", "HOME", "TMPDIR"} or self.environment.keys() & self.secret_environment:
            raise ValueError("MCP stdio environment overrides a reserved or secret binding")
        if any(not _ENVIRONMENT_NAME.fullmatch(name) for name in self.secret_environment.values()):
            raise ValueError("MCP stdio secret bindings must be deployment environment references")
        if any("\x00" in value or "${" in value for value in self.arguments):
            raise ValueError("MCP stdio arguments must be fixed deployment strings")
        if any("\x00" in value or "${" in value for value in self.environment.values()):
            raise ValueError("MCP stdio environment must be fixed deployment strings")
        return self


class McpStdioConfig(StrictModel):
    enabled: bool = False
    servers: list[McpStdioServerConfig] = Field(default_factory=list, max_length=64)
    max_frame_bytes: int = Field(default=262_144, ge=128, le=4_000_000)
    max_pending_frames: int = Field(default=4, ge=1, le=32)
    max_sessions: int = Field(default=16, ge=1, le=256)

    @model_validator(mode="after")
    def validate_servers(self) -> McpStdioConfig:
        if self.enabled and not self.servers:
            raise ValueError("enabled MCP stdio requires registered deployment servers")
        if len({server.id for server in self.servers}) != len(self.servers) or len(
            {(server.command, tuple(server.arguments)) for server in self.servers}
        ) != len(self.servers):
            raise ValueError("MCP stdio server identities and executable bindings must be unique")
        return self


class SandboxBackgroundConfig(StrictModel):
    """Explicit deployment opt-in for independently supervised internal jobs."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)

    enabled: bool = False
    max_pending: int = Field(default=1000, ge=1, le=10_000)
    lease_seconds: float = Field(default=30, gt=0, le=3600)
    io_timeout: float = Field(default=10, gt=0, le=1800)
    poll_seconds: float = Field(default=0.25, gt=0, le=60)
    scan_limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_delivery_bounds(self) -> SandboxBackgroundConfig:
        if self.io_timeout > self.lease_seconds / 2:
            raise ValueError("background io_timeout must not exceed half the job lease")
        return self


class ExecutableSourceConfig(StrictModel):
    """Deployment-owned exact script bytes; not a workflow credential or fetch rule."""

    profile: Literal["owa/executable-source/v1"] = "owa/executable-source/v1"
    id: str
    version: str
    uri: str
    language: Literal["python"] = "python"
    code: str = Field(repr=False)
    digest: str

    @model_validator(mode="after")
    def validate_capture(self) -> ExecutableSourceConfig:
        from .executable_sources import ExecutableSource

        try:
            ExecutableSource.from_value(self.model_dump(mode="json"))
        except Exception:
            raise ValueError("executable source binding is invalid") from None
        return self


class SandboxConfig(StrictModel):
    """Deployment policy for the selected framework-neutral sandbox backend."""

    enabled: bool = False
    backend: Literal["internal", "docker", "kubernetes"] = "internal"
    allow_shell: bool = False
    script_runtimes: list[str] = Field(default_factory=lambda: ["python"])
    script_sources: list[ExecutableSourceConfig] = Field(default_factory=list, max_length=32)
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
    mcp_stdio: McpStdioConfig = Field(default_factory=McpStdioConfig)
    background: SandboxBackgroundConfig = Field(default_factory=SandboxBackgroundConfig)

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
        if any(
            reference not in self.secret_environment
            for server in self.mcp_stdio.servers
            for reference in server.secret_environment.values()
        ):
            raise ValueError("MCP stdio secret references must be approved by sandbox policy")
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


class OpenApiToolBindingConfig(StrictModel):
    """Deployment-owned immutable description for a configured agent tool."""

    id: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=256)
    document_uri: str = Field(min_length=1, max_length=4096)
    document: dict[str, Any]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_description(self) -> OpenApiToolBindingConfig:
        from .openapi_client import OpenApiDescription

        captured = OpenApiDescription.capture(self.document_uri, self.document)
        if captured.digest != self.digest:
            raise ValueError("OpenAPI tool description digest differs from its deployment binding")
        return self


class ToolConfig(StrictModel):
    type: Literal["mcp", "openapi", "a2a"]
    name: str | None = None
    endpoint: str | None = None
    security_profile: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    openapi_binding: OpenApiToolBindingConfig | None = None

    @model_validator(mode="after")
    def validate_openapi_binding(self) -> ToolConfig:
        if self.openapi_binding is not None and self.type != "openapi":
            raise ValueError("OpenAPI operation bindings apply only to OpenAPI tools")
        return self


class GrpcBindingConfig(StrictModel):
    """Immutable, deployment-owned original proto and compiled descriptor pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    proto_uri: str = Field(min_length=1, max_length=2048)
    proto_filename: str = Field(min_length=1, max_length=1024)
    proto_source: str = Field(min_length=1, max_length=1_048_576)
    proto_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    descriptor_set_base64: str = Field(min_length=1, max_length=1_398_104)
    descriptor_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    endpoint: str = Field(min_length=1, max_length=2048)
    service: str = Field(min_length=1, max_length=256)
    method: str = Field(min_length=1, max_length=128)
    allow_insecure_loopback: bool = Field(default=False, strict=True)
    application_status_profile: bool = Field(default=False, strict=True)
    max_request_bytes: int = Field(default=4_000_000, gt=0, le=4_000_000, strict=True)
    max_response_bytes: int = Field(default=4_000_000, gt=0, le=4_000_000, strict=True)

    def to_binding(self, profile_id: str) -> GrpcBinding:
        from .grpc_client import GrpcBinding

        source = self.proto_source.encode("utf-8")
        descriptor = base64.b64decode(self.descriptor_set_base64, validate=True)
        if not 0 < len(source) <= 1_048_576 or not 0 < len(descriptor) <= 1_048_576:
            raise ValueError("gRPC source and descriptor must fit the finite byte limits")
        return GrpcBinding(
            id=profile_id,
            proto_uri=self.proto_uri,
            proto_filename=self.proto_filename,
            proto_source=source,
            proto_sha256=self.proto_sha256,
            descriptor_set=descriptor,
            descriptor_sha256=self.descriptor_sha256,
            endpoint=self.endpoint,
            service=self.service,
            method=self.method,
            allow_insecure_loopback=self.allow_insecure_loopback,
            application_status_profile=self.application_status_profile,
            max_request_bytes=self.max_request_bytes,
            max_response_bytes=self.max_response_bytes,
        )

    def recovery_snapshot(self, profile_id: str) -> dict[str, Any]:
        """Compact fresh byte pins; readonly recovery imports no protocol SDK."""
        source = self.proto_source.encode("utf-8")
        descriptor = base64.b64decode(self.descriptor_set_base64, validate=True)
        if (
            not 0 < len(source) <= 1_048_576
            or not 0 < len(descriptor) <= 1_048_576
            or hashlib.sha256(source).hexdigest() != self.proto_sha256
            or hashlib.sha256(descriptor).hexdigest() != self.descriptor_sha256
        ):
            raise ValueError("gRPC source and descriptor integrity is inconsistent")
        return {
            "profile": "owa/grpc-binding/v1",
            "id": profile_id,
            **self.model_dump(mode="json", exclude={"proto_source", "descriptor_set_base64"}),
        }

    @model_validator(mode="after")
    def verify_descriptor(self) -> GrpcBindingConfig:
        from .grpc_client import GrpcRegistry

        try:
            GrpcRegistry([self.to_binding("configuration-validation")])
        except (ToolError, ValueError, UnicodeError):
            raise ValueError("invalid or inconsistent gRPC deployment binding") from None
        return self


class ProtocolConfig(StrictModel):
    """Deployment policy for workflow-initiated outbound protocol calls."""

    security_profile: str | None = None
    a2a_credential_endpoints: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    openapi_credential_endpoints: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    grpc_credential_endpoints: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    grpc_bindings: dict[str, GrpcBindingConfig] = Field(default_factory=dict)

    @field_validator(
        "a2a_credential_endpoints", "openapi_credential_endpoints", "grpc_credential_endpoints"
    )
    @classmethod
    def validate_a2a_credential_endpoints(
        cls,
        value: dict[str, tuple[str, ...]],
        info: ValidationInfo,
    ) -> dict[str, tuple[str, ...]]:
        protocol = {
            "openapi_credential_endpoints": "OpenAPI",
            "grpc_credential_endpoints": "gRPC",
        }.get(info.field_name or "", "A2A")
        if len(value) > 64:
            raise ValueError(f"{protocol} credential bindings exceed the finite limit")
        for profile, endpoints in value.items():
            if not profile or len(profile) > 256 or not 1 <= len(endpoints) <= 64:
                raise ValueError(f"{protocol} credential bindings require bounded named endpoints")
            if len(set(endpoints)) != len(endpoints):
                raise ValueError(f"{protocol} credential endpoints must be unique")
            for endpoint in endpoints:
                try:
                    parsed = urlparse(endpoint)
                    _ = parsed.port
                except ValueError as error:
                    raise ValueError(f"{protocol} credential endpoint is invalid") from error
                if (
                    len(endpoint) > 4096
                    or parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.fragment
                    or any(ord(character) < 33 for character in endpoint)
                ):
                    raise ValueError(
                        f"{protocol} credential endpoints must be exact absolute HTTPS URLs"
                    )
                if info.field_name == "grpc_credential_endpoints" and (
                    parsed.path not in {"", "/"} or parsed.query
                ):
                    raise ValueError("gRPC credentials must bind an exact channel origin")
        return value

    @field_validator("grpc_bindings")
    @classmethod
    def validate_grpc_bindings(
        cls, value: dict[str, GrpcBindingConfig]
    ) -> dict[str, GrpcBindingConfig]:
        from .grpc_client import GrpcRegistry

        if len(value) > 128 or any(
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) for name in value
        ):
            raise ValueError("gRPC bindings require at most 128 bounded deployment names")
        try:
            GrpcRegistry([binding.to_binding(name) for name, binding in value.items()])
        except (ToolError, ValueError):
            raise ValueError("gRPC deployment bindings must be valid and unambiguous") from None
        return value


class RateLimitConfig(StrictModel):
    """Deployment-controlled rate limiting policy."""

    requests_per_second: float = Field(default=10.0, gt=0)
    burst: int = Field(default=20, gt=0)


class ConcurrencyLimitConfig(StrictModel):
    """Deployment-controlled concurrency limiting policy."""

    max_concurrent: int = Field(default=50, gt=0)


class TrafficLimitConfig(StrictModel):
    """Optional rate/concurrency overrides for one traffic-policy scope."""

    rate_limit: RateLimitConfig | None = None
    concurrency_limit: ConcurrencyLimitConfig | None = None

    @model_validator(mode="after")
    def validate_non_empty(self) -> TrafficLimitConfig:
        if self.rate_limit is None and self.concurrency_limit is None:
            raise ValueError("traffic limit must define rate_limit or concurrency_limit")
        return self


class TrafficEndpointLimitConfig(TrafficLimitConfig):
    """Add admission limits for a normalized request path prefix."""

    path_prefix: str

    @field_validator("path_prefix")
    @classmethod
    def validate_path_prefix(cls, value: str) -> str:
        selected = value.strip()
        if not selected.startswith("/") or len(selected) > 256:
            raise ValueError("traffic endpoint path_prefix must start with '/' and stay bounded")
        return selected.rstrip("/") or "/"


class TrafficPrincipalLimitConfig(TrafficLimitConfig):
    """Add admission limits for an authenticated deployment principal."""

    principal: str

    @field_validator("principal")
    @classmethod
    def validate_principal(cls, value: str) -> str:
        selected = value.strip()
        if not selected or len(selected) > 256:
            raise ValueError("traffic principal must be a bounded non-empty value")
        return selected


class TrafficPolicyConfig(StrictModel):
    """Deployment-controlled traffic policy for rate limits, concurrency limits,
    and burst/admission control.

    Authentication/authorization profiles must not own traffic management.
    """

    enabled: bool = False
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    concurrency_limit: ConcurrencyLimitConfig = Field(default_factory=ConcurrencyLimitConfig)
    endpoint_limits: list[TrafficEndpointLimitConfig] = Field(default_factory=list)
    principal_limits: list[TrafficPrincipalLimitConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_scopes(self) -> TrafficPolicyConfig:
        endpoint_prefixes = [item.path_prefix for item in self.endpoint_limits]
        if len(endpoint_prefixes) != len(set(endpoint_prefixes)):
            raise ValueError("traffic endpoint path_prefix values must be unique")
        principals = [item.principal for item in self.principal_limits]
        if len(principals) != len(set(principals)):
            raise ValueError("traffic principal values must be unique")
        return self


class SecurityHeadersConfig(StrictModel):
    """Configuration for the default HTTP security response headers."""

    enabled: bool = True
    content_type_options: str = Field(default="nosniff", min_length=1)
    frame_options: str = Field(default="DENY", min_length=1)
    content_security_policy: str = Field(default="default-src 'none'", min_length=1)
    hsts_enabled: bool = True
    hsts_max_age_seconds: int = Field(default=31_536_000, ge=0)
    hsts_include_subdomains: bool = True
    hsts_preload: bool = False


class ServerConfig(StrictModel):
    host: str = "0.0.0.0"
    port: int = 8080
    max_request_bytes: int = 1_048_576
    api_security_profile: str | None = None
    cors_origins: list[str] = Field(default_factory=list)
    cors_methods: list[str] = Field(
        default_factory=lambda: ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    )
    cors_headers: list[str] = Field(default_factory=lambda: ["*"])
    cors_allow_credentials: bool = False
    security_headers: SecurityHeadersConfig = Field(default_factory=SecurityHeadersConfig)


class ObservabilityConfig(StrictModel):
    log_level: str = "INFO"
    structured_logging: bool = False
    log_format: str = "json"  # "json" or "text"


class EventConfig(StrictModel):
    """Deployment-owned bounds for embedded generic event delivery."""

    backend: Literal["embedded", "memory"] = "embedded"
    max_event_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)
    max_retained_events: int = Field(default=10_000, ge=1, le=1_000_000)
    max_retained_bytes: int = Field(default=67_108_864, ge=1, le=1_073_741_824)
    max_subscriptions: int = Field(default=1000, ge=1, le=100_000)
    max_outbox: int = Field(default=10_000, ge=1, le=1_000_000)
    scan_batch: int = Field(default=128, ge=1, le=1000)
    retention_seconds: float = Field(default=86_400, ge=1, le=31_536_000)
    poll_interval_seconds: float = Field(default=0.05, ge=0.01, le=5)


class ExecutionTimingConfig(StrictModel):
    """Deployment calendar and bounded embedded dispatch controls."""

    timezone: str = "UTC"
    max_pending_timers: int = Field(default=1000, ge=1, le=100_000)
    max_concurrency: int = Field(default=16, ge=1, le=1000)
    schedule_batch_size: int = Field(default=128, ge=1, le=1000)
    poll_interval_seconds: float = Field(default=0.05, ge=0.01, le=5)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an available IANA timezone") from exc
        return value


class ChildWorkflowConfig(StrictModel):
    """Deployment limits for one root invocation's child execution tree."""

    max_depth: int = Field(default=32, ge=1, le=64)
    max_children: int = Field(default=1000, ge=1, le=100_000)
    max_concurrency: int = Field(default=16, ge=1, le=1000)


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
    execution_authorization: ExecutionAuthorizationConfig = Field(
        default_factory=ExecutionAuthorizationConfig
    )
    traffic_policy: TrafficPolicyConfig = Field(default_factory=TrafficPolicyConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    tools: list[ToolConfig] = Field(default_factory=list)
    server: ServerConfig = Field(default_factory=ServerConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    events: EventConfig = Field(default_factory=EventConfig)
    execution_timing: ExecutionTimingConfig = Field(default_factory=ExecutionTimingConfig)
    child_workflows: ChildWorkflowConfig = Field(default_factory=ChildWorkflowConfig)

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

        def require_header_profile(field: str, name: str | None, *, outbound: bool = False) -> None:
            if name is None:
                return
            profile = self.security.profiles.get(name)
            if profile is None:
                raise ValueError(f"{field} references unknown security profile: {name}")
            permitted = (
                header_capable | {"oauth2_client_credentials", "mtls"}
                if outbound
                else header_capable
            )
            if profile.type not in permitted:
                raise ValueError(
                    f"{field} must reference a security profile of type "
                    f"{' or '.join(sorted(permitted))}"
                )
            if (
                outbound
                and profile.type == "oauth2_client_credentials"
                and not profile.allowed_resource_endpoints
            ):
                raise ValueError(f"{field} requires deployment-approved OAuth resource endpoints")

        require_header_profile("server.api_security_profile", self.server.api_security_profile)
        require_header_profile(
            "approvals.operator_security_profile", self.approvals.operator_security_profile
        )
        if self.approvals.actor_security_profiles:
            from .errors import ApprovalValidationError
            from .protected_approval_policy import actor_identity

            identities = set()
            for name in self.approvals.actor_security_profiles:
                profile = self.security.profiles.get(name)
                if profile is None or profile.type != "bearer" or not profile.principal:
                    raise ValueError(
                        "approval actors require named bearer profiles with principals"
                    )
                try:
                    identity = actor_identity(profile.principal)
                except ApprovalValidationError:
                    raise ValueError("approval actor principal is invalid") from None
                if identity in identities:
                    raise ValueError("approval actor principals must be unique")
                identities.add(identity)
        require_header_profile(
            "protocols.security_profile", self.protocols.security_profile, outbound=True
        )
        for profile_name in self.protocols.a2a_credential_endpoints:
            require_header_profile(
                "protocols.a2a_credential_endpoints", profile_name, outbound=True
            )
        for profile_name in self.protocols.openapi_credential_endpoints:
            require_header_profile(
                "protocols.openapi_credential_endpoints", profile_name, outbound=True
            )
        for profile_name in self.protocols.grpc_credential_endpoints:
            require_header_profile(
                "protocols.grpc_credential_endpoints", profile_name, outbound=True
            )
        for catalog_name, catalog in self.workflow.external_catalogs.items():
            require_header_profile(
                f"external catalog '{catalog_name}' authentication",
                catalog.authentication.security_profile,
            )
        for index, tool in enumerate(self.tools):
            require_header_profile(f"tools[{index}]", tool.security_profile, outbound=True)
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
            except yaml.YAMLError:
                raise ConfigurationError("invalid YAML configuration") from None
        _apply_environment(raw)
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise ConfigurationError(
                "invalid runtime configuration",
                details={
                    "errors": exc.errors(
                        include_input=False, include_context=False, include_url=False
                    )
                },
            ) from None


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
