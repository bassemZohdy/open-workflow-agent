from __future__ import annotations

from pathlib import Path

import pytest
from open_workflow_agent.config import (
    A2AConfig,
    A2ASkillConfig,
    DockerSandboxConfig,
    ExternalCatalogConfig,
    KubernetesSandboxConfig,
    RuntimeConfig,
    SandboxConfig,
)


def test_defaults_when_no_file_exists(tmp_path: Path) -> None:
    config = RuntimeConfig.from_file(tmp_path / "missing.yaml")
    assert config.model.provider == "litellm"
    assert config.model.name == "fake/default"
    assert config.sandbox.enabled is False
    assert config.approvals.enabled is False


def test_explicit_path_loads_yaml(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(
        "model:\n  provider: litellm\n  name: openai/gpt-test\n",
        encoding="utf-8",
    )
    config = RuntimeConfig.from_file(path)
    assert config.model.provider == "litellm"
    assert config.model.name == "openai/gpt-test"


def test_owa_config_file_env_selects_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "selected.yaml"
    other = tmp_path / "other.yaml"
    selected.write_text("observability:\n  log_level: DEBUG\n", encoding="utf-8")
    other.write_text("observability:\n  log_level: WARNING\n", encoding="utf-8")
    monkeypatch.setenv("OWA_CONFIG_FILE", str(selected))
    config = RuntimeConfig.from_file(None)
    assert config.observability.log_level == "DEBUG"
    monkeypatch.setenv("OWA_CONFIG_FILE", str(other))
    assert RuntimeConfig.from_file(None).observability.log_level == "WARNING"


def test_environment_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(
        "observability:\n  log_level: DEBUG\nsandbox:\n  enabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OWA__OBSERVABILITY__LOG_LEVEL", "ERROR")
    monkeypatch.setenv("OWA__SANDBOX__ENABLED", "true")
    config = RuntimeConfig.from_file(path)
    # YAML sets the value, environment wins.
    assert config.observability.log_level == "ERROR"
    assert config.sandbox.enabled is True


def test_environment_parses_yaml_scalars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("model:\n  provider: fake\n", encoding="utf-8")
    monkeypatch.setenv("OWA__SANDBOX__TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("OWA__SANDBOX__MAX_OUTPUT_BYTES", "2048")
    monkeypatch.setenv("OWA__SANDBOX__SCRIPT_RUNTIMES", "[python]")
    monkeypatch.setenv("OWA__SANDBOX__ALLOW_SHELL", "true")
    monkeypatch.setenv("OWA__SANDBOX__CPU_SECONDS", "null")
    config = RuntimeConfig.from_file(path)
    assert config.sandbox.timeout_seconds == 12.5
    assert config.sandbox.max_output_bytes == 2048
    assert config.sandbox.script_runtimes == ["python"]
    assert config.sandbox.allow_shell is True
    assert config.sandbox.cpu_seconds is None


def test_environment_creates_nested_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("model:\n  provider: fake\n", encoding="utf-8")
    monkeypatch.setenv("OWA__MODEL__OPTIONS__API_BASE", "http://127.0.0.1:11434")
    config = RuntimeConfig.from_file(path)
    assert config.model.options["api_base"] == "http://127.0.0.1:11434"


def test_owa_config_file_is_not_a_config_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("model:\n  provider: fake\n", encoding="utf-8")
    monkeypatch.setenv("OWA_CONFIG_FILE", str(path))
    config = RuntimeConfig.from_file(None)
    assert not hasattr(config, "config_file")


def test_invalid_yaml_raises_configuration_error(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("model:\n  - broken\n    nested: [\n", encoding="utf-8")
    from open_workflow_agent.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_file(path)


def test_non_object_root_raises_configuration_error(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    from open_workflow_agent.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_file(path)


def test_unknown_key_raises_configuration_error(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text("not_a_real_section:\n  value: 1\n", encoding="utf-8")
    from open_workflow_agent.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_file(path)


def test_security_profiles_round_trip_every_profile_type() -> None:
    config = RuntimeConfig.model_validate(
        {
            "security": {
                "profiles": {
                    "bearer-profile": {"type": "bearer", "token": {"from_env": "TOKEN_ENV"}},
                    "api-key-profile": {"type": "api_key", "key": {"from_env": "KEY_ENV"}},
                    "oauth-profile": {
                        "type": "oauth2_client_credentials",
                        "token_url": "https://auth.example.com/token",
                        "client_id": {"from_env": "CLIENT_ID_ENV"},
                        "client_secret": {"from_env": "CLIENT_SECRET_ENV"},
                    },
                    "mtls-profile": {
                        "type": "mtls",
                        "certificate": {"from_env": "CERT_ENV"},
                        "private_key": {"from_env": "KEY_ENV"},
                    },
                }
            }
        }
    )
    assert config.security.profiles["bearer-profile"].type == "bearer"
    assert config.security.profiles["api-key-profile"].type == "api_key"
    assert config.security.profiles["oauth-profile"].type == "oauth2_client_credentials"
    assert config.security.profiles["mtls-profile"].type == "mtls"


def test_a2a_security_profile_must_reference_known_profile(tmp_path: Path) -> None:
    from open_workflow_agent.errors import ConfigurationError

    path = tmp_path / "agent.yaml"
    path.write_text("a2a:\n  security_profile: missing\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_file(path)


def test_a2a_security_profile_must_be_bearer_type(tmp_path: Path) -> None:
    from open_workflow_agent.errors import ConfigurationError

    path = tmp_path / "agent.yaml"
    path.write_text(
        "security:\n"
        "  profiles:\n"
        "    api-key-profile:\n"
        "      type: api_key\n"
        "      key:\n"
        "        from_env: KEY_ENV\n"
        "a2a:\n"
        "  security_profile: api-key-profile\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_file(path)


def test_a2a_auth_token_field_is_no_longer_accepted(tmp_path: Path) -> None:
    from open_workflow_agent.errors import ConfigurationError

    path = tmp_path / "agent.yaml"
    path.write_text("a2a:\n  auth_token: secret\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_file(path)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"allowed_hosts": ["example.test", "example.test"]}, "duplicates"),
        ({"allowed_hosts": [""]}, "empty"),
        ({"allowed_endpoints": ["http://example.test"]}, "absolute HTTPS"),
        ({"allowed_endpoints": ["https://user:pass@example.test"]}, "credentials"),
        ({"timeout_seconds": 0}, "greater than zero"),
        ({"max_response_bytes": 0}, "greater than zero"),
        ({"cache_ttl_seconds": -1}, "cannot be negative"),
        ({"max_cache_age_seconds": 0}, "greater than zero"),
        ({"max_cache_entries": 0}, "greater than zero"),
        ({"integrity_pins": {"": "0" * 64}}, "keys cannot be empty"),
        ({"integrity_pins": {"fn": "not-a-digest"}}, "SHA-256"),
        ({"follow_redirects": True}, "redirects"),
        ({"verify_tls": False}, "TLS"),
    ],
)
def test_external_catalog_validation_rejects_unsafe_values(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ExternalCatalogConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"id": "", "workflow": "main"}, "identifier"),
        ({"id": "UPPER", "workflow": "main"}, "identifier"),
        ({"id": "main", "workflow": ""}, "registered workflow"),
        ({"id": "main", "workflow": "main", "name": ""}, "must not be empty"),
        ({"id": "main", "workflow": "main", "tags": ["x", "x"]}, "unique"),
    ],
)
def test_a2a_skill_validation_rejects_unsafe_values(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        A2ASkillConfig.model_validate(payload)


def test_a2a_validation_normalizes_paths_and_rejects_duplicate_skills() -> None:
    assert A2AConfig.model_validate({"path": "/a2a/"}).path == "/a2a"
    assert A2AConfig.model_validate(
        {"public_base_url": "https://example.test/"}
    ).public_base_url == ("https://example.test")
    with pytest.raises(ValueError, match="unique"):
        A2AConfig.model_validate(
            {
                "skills": [
                    {"id": "one", "workflow": "first"},
                    {"id": "one", "workflow": "second"},
                ]
            }
        )
    with pytest.raises(ValueError, match="absolute http"):
        A2AConfig.model_validate({"public_base_url": "ftp://example.test"})
    for value in (
        "https://user:password@example.test",
        "https://example.test?token=secret",
        "https://example.test/#fragment",
        "https://[invalid",
    ):
        with pytest.raises(ValueError, match="public_base_url"):
            A2AConfig.model_validate({"public_base_url": value})


@pytest.mark.parametrize(
    ("factory", "payload", "message"),
    [
        (DockerSandboxConfig, {"controller_socket": "relative"}, "absolute path"),
        (DockerSandboxConfig, {"allowed_images": ["busybox"]}, "sha256"),
        (DockerSandboxConfig, {"run_as_user": "0"}, "non-root"),
        (KubernetesSandboxConfig, {"controller_url": "https://example.test"}, "loopback"),
        (KubernetesSandboxConfig, {"secret_keys": ["bad-name"]}, "environment-style"),
        (KubernetesSandboxConfig, {"secret_keys": ["TOKEN"]}, "secret_name"),
    ],
)
def test_sandbox_controller_validation_rejects_unsafe_values(
    factory: object, payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        factory.model_validate(payload)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"script_runtimes": []}, "at least one"),
        ({"script_runtimes": ["ruby"]}, "unsupported"),
        ({"script_runtimes": ["python", "python"]}, "duplicates"),
        ({"timeout_seconds": 0}, "greater than zero"),
        ({"max_input_bytes": 0}, "byte limits"),
        ({"cpu_seconds": 0}, "resource limits"),
        ({"workspace_root": "relative"}, "absolute path"),
        ({"executable_search_path": "relative"}, "absolute paths"),
        ({"inherited_environment": ["BAD-NAME"]}, "environment variable"),
        ({"inherited_environment": ["TOKEN"], "secret_environment": ["TOKEN"]}, "overlap"),
        ({"enabled": True, "backend": "docker"}, "approved image"),
        (
            {"enabled": True, "backend": "kubernetes", "kubernetes": {"allowed_images": []}},
            "approved image",
        ),
    ],
)
def test_sandbox_validation_rejects_unsafe_values(payload: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SandboxConfig.model_validate(payload)
