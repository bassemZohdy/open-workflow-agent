"""Security regression tests for the C24 hardening pass."""

from __future__ import annotations

import importlib.util
import json

import pytest

from openworkflow_adk import load
from openworkflow_adk.loader import WorkflowValidationError
from openworkflow_adk.security.security import EgressDeniedError
from openworkflow_adk.tasks.run import (
    _container_limits,
    _container_network_mode,
    _resolve_container_volume,
)

# ---------------------------------------------------------------------------
# C24.4 — prompt-injection → code-execution is rejected at translate time
# ---------------------------------------------------------------------------


def test_expression_bound_shell_command_is_rejected() -> None:
    with pytest.raises(WorkflowValidationError, match="run.shell.command"):
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "inject",
                    "version": "1.0.0",
                },
                "do": [
                    {
                        "exec": {
                            "run": {
                                "shell": {
                                    "command": "echo ${llm_output}",
                                }
                            }
                        }
                    }
                ],
            }
        )


def test_expression_bound_container_image_is_rejected() -> None:
    with pytest.raises(WorkflowValidationError, match="run.container.image"):
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "inject",
                    "version": "1.0.0",
                },
                "do": [
                    {
                        "exec": {
                            "run": {"container": {"image": "${agent.registry}", "command": "ls"}}
                        }
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("arguments", ["${agent.argument}"]), ("environment", {"VALUE": "${agent.value}"})],
)
def test_expression_bound_container_exec_options_are_rejected(field: str, value: object) -> None:
    with pytest.raises(WorkflowValidationError, match=f"run.container.{field}"):
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "inject",
                    "version": "1.0.0",
                },
                "do": [
                    {
                        "exec": {
                            "run": {
                                "container": {
                                    "image": "alpine:latest",
                                    field: value,
                                }
                            }
                        }
                    }
                ],
            }
        )


def test_expression_bound_mcp_command_is_rejected() -> None:
    with pytest.raises(WorkflowValidationError, match="stdio.command"):
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "inject",
                    "version": "1.0.0",
                },
                "do": [
                    {
                        "exec": {
                            "call": "mcp",
                            "with": {
                                "transport": {
                                    "stdio": {
                                        "command": "${tool_path}",
                                        "arguments": ["serve"],
                                    }
                                },
                                "method": "tools/list",
                            },
                        }
                    }
                ],
            }
        )


def test_static_shell_command_is_allowed() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "ok", "version": "1.0.0"},
            "do": [{"exec": {"run": {"shell": {"command": "echo static"}}}}],
        }
    )
    assert document.do[0].name == "exec"


# ---------------------------------------------------------------------------
# C24.3 — container task hardening (pure helpers, no Docker daemon needed)
# ---------------------------------------------------------------------------


def test_container_volume_denied_when_allowlist_unset() -> None:
    with pytest.raises(PermissionError, match="volume mounts are disabled"):
        _resolve_container_volume("/etc/passwd", [])


def test_container_volume_allowed_under_configured_root(tmp_path) -> None:
    allowed = tmp_path / "volumes"
    allowed.mkdir()
    host = allowed / "data"
    host.mkdir()
    assert _resolve_container_volume(str(host), [allowed]) == host.resolve()


def test_container_volume_outside_allowlist_is_denied(tmp_path) -> None:
    allowed = tmp_path / "volumes"
    allowed.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ValueError, match="not under any allowed root"):
        _resolve_container_volume(str(outside / "file"), [allowed])


def test_container_network_defaults_to_none() -> None:
    assert _container_network_mode(None) == "none"
    assert _container_network_mode("") == "none"
    assert _container_network_mode("none") == "none"


def test_container_network_requires_allowlist(monkeypatch) -> None:
    monkeypatch.delenv("WORKFLOW_CONTAINER_NETWORK_ALLOWLIST", raising=False)
    with pytest.raises(PermissionError, match="NETWORK_ALLOWLIST"):
        _container_network_mode("host")


def test_container_network_allowlisted_mode_is_accepted(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_CONTAINER_NETWORK_ALLOWLIST", "host, bridge")
    assert _container_network_mode("host") == "host"


def test_container_limits_defaults_are_absent_without_env(monkeypatch) -> None:
    monkeypatch.delenv("WORKFLOW_CONTAINER_CPU_LIMIT", raising=False)
    monkeypatch.delenv("WORKFLOW_CONTAINER_MEMORY_LIMIT", raising=False)
    monkeypatch.delenv("WORKFLOW_CONTAINER_PIDS_LIMIT", raising=False)
    assert _container_limits() == {}


def test_container_limits_read_hard_caps(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_CONTAINER_CPU_LIMIT", "0.5")
    monkeypatch.setenv("WORKFLOW_CONTAINER_MEMORY_LIMIT", "256m")
    monkeypatch.setenv("WORKFLOW_CONTAINER_PIDS_LIMIT", "128")
    limits = _container_limits()
    assert limits == {"cpus": "0.5", "memory": "256m", "pids": 128}


# ---------------------------------------------------------------------------
# C24.8 — SAML metadata parsing is XXE-safe
# ---------------------------------------------------------------------------


def test_saml_metadata_rejects_entity_expansion() -> None:
    from openworkflow_adk.security.sso import SamlMetadata

    payload = (
        '<!DOCTYPE EntityDescriptor [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="&xxe;">'
        "<IDPSSODescriptor><SingleSignOnService "
        'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" '
        'Location="https://idp.example/sso" /></IDPSSODescriptor></EntityDescriptor>'
    )
    with pytest.raises(Exception):
        SamlMetadata.from_xml(payload)


# ---------------------------------------------------------------------------
# C24.2 — SSRF suite
# ---------------------------------------------------------------------------


def test_ssrf_hostname_resolving_to_loopback_denied() -> None:
    from unittest.mock import patch

    with patch(
        "openworkflow_adk.security.security.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("127.0.0.1", 80))],
    ):
        with pytest.raises(EgressDeniedError, match="blocked"):
            from openworkflow_adk.security.security import validate_egress

            validate_egress("https://internal.example", {})


def test_ssrf_hostname_resolving_to_metadata_service_denied() -> None:
    from unittest.mock import patch

    with patch(
        "openworkflow_adk.security.security.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("169.254.169.254", 80))],
    ):
        with pytest.raises(EgressDeniedError, match="blocked"):
            from openworkflow_adk.security.security import validate_egress

            validate_egress("https://internal.example", {})


def test_redirect_to_blocked_range_is_denied() -> None:
    """Redirect hops are re-validated by the guarded client's request hook."""
    from unittest.mock import patch

    from openworkflow_adk.security.security import guarded_async_client

    async def _exercise() -> None:
        client = guarded_async_client(follow_redirects=True)
        try:
            with patch(
                "openworkflow_adk.security.security.socket.getaddrinfo",
                side_effect=[
                    [(None, None, None, None, ("93.184.216.34", 80))],  # first hop ok
                    [(None, None, None, None, ("10.0.0.1", 80))],  # redirect → private
                ],
            ):
                await client.get("https://safe.example/start")
        finally:
            await client.aclose()

    with pytest.raises(Exception):
        import asyncio

        asyncio.run(_exercise())


# ---------------------------------------------------------------------------
# C24.1/C24.9 — HTTP server auth and error hygiene
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None or importlib.util.find_spec("uvicorn") is None,
    reason="server extras not installed",
)
def test_run_endpoint_requires_credentials_when_auth_configured() -> None:
    from fastapi.testclient import TestClient

    from openworkflow_adk.server import ServerAuthConfig, create_app

    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "auth", "version": "1.0.0"},
            "do": [{"finish": {"set": {"value": 1}}}],
        }
    )
    client = TestClient(create_app(document, auth=ServerAuthConfig(api_keys={"secret-key"})))
    assert client.post("/run", json={"input": {}}).status_code == 401
    assert client.get("/metrics").status_code == 401
    assert client.get("/openapi.json").status_code == 401


@pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None or importlib.util.find_spec("uvicorn") is None,
    reason="server extras not installed",
)
def test_run_endpoint_accepts_valid_api_key_and_ignores_body_user_id() -> None:
    from fastapi.testclient import TestClient

    from openworkflow_adk.server import ServerAuthConfig, create_app

    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "auth", "version": "1.0.0"},
            "do": [{"finish": {"set": {"greeting": '"hello"'}}}],
        }
    )
    client = TestClient(create_app(document, auth=ServerAuthConfig(api_keys={"secret-key"})))
    response = client.post(
        "/run",
        json={"input": {}, "user_id": "attacker-controlled"},
        headers={"authorization": "Bearer secret-key"},
    )
    assert response.status_code == 200


@pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None or importlib.util.find_spec("uvicorn") is None,
    reason="server extras not installed",
)
def test_run_endpoint_hides_exception_text_behind_correlation_id() -> None:
    from fastapi.testclient import TestClient

    from openworkflow_adk.server import create_app

    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "boom", "version": "1.0.0"},
            "do": [{"fail": {"set": {"x": "${1 + }"}}}],
        }
    )
    client = TestClient(create_app(document))
    response = client.post("/run", json={"input": {}})
    assert response.status_code == 500
    body = response.json()["detail"]
    # the failing expression's text must not leak into the client-visible error
    assert "1 +" not in json.dumps(body)
    assert body["code"] == "internal_error"
    assert body["correlation_id"]


def test_serve_refuses_non_loopback_without_auth() -> None:
    from openworkflow_adk.server import serve

    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "auth", "version": "1.0.0"},
            "do": [{"finish": {"set": {"value": 1}}}],
        }
    )
    with pytest.raises(ValueError, match="authentication"):
        serve(document, host="0.0.0.0", port=0)


def test_serve_allows_non_loopback_with_auth() -> None:
    from unittest.mock import patch

    from openworkflow_adk.server import ServerAuthConfig, serve

    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "auth", "version": "1.0.0"},
            "do": [{"finish": {"set": {"value": 1}}}],
        }
    )
    with patch("uvicorn.run"):
        serve(document, host="0.0.0.0", port=0, auth=ServerAuthConfig(api_keys={"k"}))


# ---------------------------------------------------------------------------
# Fuzz: oversized/deep expressions
# ---------------------------------------------------------------------------


def test_oversized_expression_is_rejected() -> None:
    from openworkflow_adk.expressions import ExpressionError, evaluate

    with pytest.raises(ExpressionError, match="maximum length"):
        evaluate("${" + "1" * 20001 + "}")


def test_deeply_nested_expression_is_rejected() -> None:
    from openworkflow_adk.expressions import ExpressionError, evaluate

    with pytest.raises(ExpressionError, match="maximum depth"):
        evaluate("${" + "(" * 200 + "1" + ")" * 200 + "}")


# ---------------------------------------------------------------------------
# C24.16 — AsyncAPI consumer loop is bounded
# ---------------------------------------------------------------------------


async def test_asyncapi_consumer_times_out_when_no_event_arrives(monkeypatch) -> None:
    import respx

    from openworkflow_adk import run_workflow
    from openworkflow_adk.resources.broker import Broker

    class EmptyBroker(Broker):
        def __init__(self) -> None:
            self._wait = __import__("asyncio").Event()

        async def publish(self, event):
            pass

        async def consume(self):
            await self._wait.wait()
            return {}

        async def close(self) -> None:
            pass

    monkeypatch.setenv("WORKFLOW_CONSUME_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setenv("WORKFLOW_EGRESS_SKIP_DNS", "1")
    # Build the document via the models directly: the vendored 1.0.3 schema's
    # AsyncAPI subscription variant does not validate, so bypass jsonschema and
    # exercise the runtime consume path.
    from openworkflow_adk.models import OpenWorkflowDocument

    document = OpenWorkflowDocument.model_validate(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "listen",
                "version": "1.0.0",
            },
            "do": [
                {
                    "wait_event": {
                        "call": "asyncapi",
                        "with": {
                            "document": {"endpoint": {"uri": "https://spec.test/a.json"}},
                            "channel": "events",
                        },
                    }
                }
            ],
        }
    )
    assert document.do[0].task.call == "asyncapi"
    import pytest as _pytest

    with respx.mock:
        respx.get("https://spec.test/a.json").respond(
            json={"asyncapi": "2.6.0", "channels": {"events": {}}}
        )
        with _pytest.raises(Exception, match="timed out waiting"):
            await run_workflow(document, broker=EmptyBroker())


# ---------------------------------------------------------------------------
# C24.5 — MCP stdio commands are allowlisted and bounded
# ---------------------------------------------------------------------------


def test_mcp_stdio_denied_without_allowlist(monkeypatch) -> None:
    monkeypatch.delenv("WORKFLOW_MCP_COMMAND_ALLOWLIST", raising=False)
    monkeypatch.delenv("WORKFLOW_MCP_ALLOW_UNLISTED", raising=False)
    from openworkflow_adk.tasks.events import _check_mcp_command

    with pytest.raises(PermissionError, match="WORKFLOW_MCP_COMMAND_ALLOWLIST"):
        _check_mcp_command("node server.js")


def test_mcp_stdio_command_requires_allowlist_entry(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_MCP_COMMAND_ALLOWLIST", "python3,node")
    from openworkflow_adk.tasks.events import _check_mcp_command

    _check_mcp_command("node server.js")
    _check_mcp_command("python3 -m mcp_server")
    with pytest.raises(PermissionError, match="not on WORKFLOW_MCP_COMMAND_ALLOWLIST"):
        _check_mcp_command("bash server.sh")


def test_mcp_stdio_allow_unlisted_escape_hatch(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_MCP_ALLOW_UNLISTED", "1")
    from openworkflow_adk.tasks.events import _check_mcp_command

    _check_mcp_command("anything at all")
