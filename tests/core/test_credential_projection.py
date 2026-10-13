"""Actual selected-credential direct echoes, with bounded no-paid transports."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest
from open_workflow_agent.config import SandboxConfig
from open_workflow_agent.credential_guard import (
    LiteralCredentialGuard,
    active_projection_guard,
    credential_scope,
)
from open_workflow_agent.errors import SandboxPolicyError, ToolError
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.sandbox.backends.docker import DockerSandboxBackend
from open_workflow_agent.sandbox.backends.internal import InternalSandboxBackend
from open_workflow_agent.sandbox.backends.kubernetes import KubernetesSandboxBackend
from open_workflow_agent.sandbox.contract import SandboxExecutionRequest, SandboxSecretReference
from open_workflow_agent.sandbox.manager import SandboxManager
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig


def authentication(
    monkeypatch, kind="bearer", *, name="selected", value="controlled-private-credential-391"
):
    variable = "OWA_PROJECTION_" + name.upper()
    monkeypatch.setenv(variable, value)
    fields = {"type": kind, "principal": name}
    fields.update(
        {"token": {"from_env": variable}}
        if kind == "bearer"
        else {"header": "X-Private-Key", "key": {"from_env": variable}}
    )
    config = SecurityConfig.model_validate({"profiles": {name: fields}})
    return ProfileAuthentication(config, name), value


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bearer", "api_key"])
@pytest.mark.parametrize("output", ["content", "response", "raw"])
async def test_actual_named_http_credential_echo_is_removed_before_public_projection(
    monkeypatch, kind, output
):
    auth, secret = authentication(monkeypatch, kind)
    calls = []

    def dispatch(request):
        calls.append(request)
        header = "Authorization" if kind == "bearer" else "X-Private-Key"
        assert secret in request.headers[header]
        return httpx.Response(
            200,
            json={"echo": secret, "nested": [None, {"same": secret}], "safe": 37},
            headers={
                "X-Echo": secret,
                "Set-Cookie": "private=" + secret,
                "Authorization": "Bearer " + secret,
            },
        )

    client = HttpClient(
        authentication=auth, transport=httpx.MockTransport(dispatch), allowed_hosts={"peer.test"}
    )
    result = await client.request("GET", "https://peer.test/business", output=output)
    rendered = base64.b64decode(result).decode() if output == "raw" else json.dumps(result)
    assert secret not in rendered and "[REDACTED]" in rendered and len(calls) == 1
    value = (
        json.loads(rendered)
        if output == "raw"
        else (result["body"] if output == "response" else result)
    )
    assert value["nested"][0] is None and value["safe"] == 37
    if output == "response":
        assert "authorization" not in result["headers"] and "set-cookie" not in result["headers"]
        assert "authorization" not in result["request"]["headers"]
    assert active_projection_guard() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["remote_status", "client_transport"])
async def test_remote_and_client_errors_cannot_retain_selected_credentials(monkeypatch, failure):
    auth, secret = authentication(monkeypatch)

    def dispatch(request):
        if failure == "client_transport":
            raise httpx.ConnectError("controlled peer echoed " + secret, request=request)
        return httpx.Response(403, text="rejected " + secret, headers={"X-Echo": secret})

    protocols = ProtocolServices(
        http=HttpClient(authentication=auth, transport=httpx.MockTransport(dispatch))
    )
    with pytest.raises(ToolError) as error:
        await protocols.call("http", {"endpoint": "https://peer.test/business", "method": "POST"})
    assert secret not in str(error.value) and secret not in json.dumps(error.value.as_dict())
    if failure == "remote_status":
        assert (
            error.value.details["status"] == 403 and "[REDACTED]" in error.value.details["detail"]
        )
    assert active_projection_guard() is None


@pytest.mark.asyncio
async def test_oauth_token_and_client_secret_echoes_are_guarded_on_fetch_and_cache(monkeypatch):
    client_secret, token = "private-oauth-client-secret-791", "private-oauth-access-token-629"
    monkeypatch.setenv("OWA_PROJECTION_CLIENT", "client-id")
    monkeypatch.setenv("OWA_PROJECTION_SECRET", client_secret)
    config = SecurityConfig.model_validate(
        {
            "profiles": {
                "oauth": {
                    "type": "oauth2_client_credentials",
                    "token_url": "https://issuer.test/token",
                    "client_id": {"from_env": "OWA_PROJECTION_CLIENT"},
                    "client_secret": {"from_env": "OWA_PROJECTION_SECRET"},
                    "allowed_resource_endpoints": ["https://peer.test/business"],
                }
            }
        }
    )
    calls = []

    def dispatch(request):
        calls.append(str(request.url))
        if request.url.host == "issuer.test":
            assert client_secret in request.content.decode()
            return httpx.Response(
                200, json={"access_token": token, "token_type": "Bearer", "expires_in": 300}
            )
        assert request.headers["Authorization"] == "Bearer " + token
        return httpx.Response(
            200, json={"token": token, "clientEcho": client_secret, "business": True}
        )

    client = HttpClient(
        transport=httpx.MockTransport(dispatch), allowed_hosts={"issuer.test", "peer.test"}
    )
    auth = ProfileAuthentication(config, "oauth", token_http=client)
    for _ in range(2):
        result = await client.request("POST", "https://peer.test/business", _authentication=auth)
        assert result == {"token": "[REDACTED]", "clientEcho": "[REDACTED]", "business": True}
    assert (
        calls.count("https://issuer.test/token") == 1
        and calls.count("https://peer.test/business") == 2
    )
    assert active_projection_guard() is None


@pytest.mark.asyncio
async def test_concurrent_selected_profiles_have_task_local_projection_and_cleanup(monkeypatch):
    a, secret_a = authentication(monkeypatch, name="a", value="private-a-selected-credential-457")
    b, secret_b = authentication(monkeypatch, name="b", value="private-b-selected-credential-263")
    reached, release = 0, asyncio.Event()

    async def dispatch(request):
        nonlocal reached
        reached += 1
        if reached == 2:
            release.set()
        await release.wait()
        token = request.headers["Authorization"][7:]
        return httpx.Response(200, json={"echo": token, "business": request.url.path})

    client = HttpClient(transport=httpx.MockTransport(dispatch))
    async with asyncio.timeout(5):
        results = await asyncio.gather(
            client.request("GET", "https://peer.test/a", _authentication=a),
            client.request("GET", "https://peer.test/b", _authentication=b),
        )
    assert results == [
        {"echo": "[REDACTED]", "business": "/a"},
        {"echo": "[REDACTED]", "business": "/b"},
    ]
    assert secret_a not in json.dumps(results) and secret_b not in json.dumps(results)
    assert active_projection_guard() is None
    with credential_scope() as independent:
        assert independent.text(secret_a) == secret_a


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["destination", "credential"])
async def test_policy_denial_precedes_actual_secret_resolution_and_business_send(
    monkeypatch, stage
):
    auth, _ = authentication(monkeypatch)
    calls, resolutions = [], []

    async def denied(*args):
        raise ToolError("controlled permission denied", details={"stage": "authorization"})

    def resolve(reference):
        resolutions.append(reference)
        pytest.fail("denied request attempted secret resolution")

    monkeypatch.setattr("open_workflow_agent.security.resolve_secret", resolve)
    protocols = ProtocolServices(
        http=HttpClient(transport=httpx.MockTransport(lambda request: calls.append(request))),
        authorize_destination=denied if stage == "destination" else None,
        authorize_credential=denied if stage == "credential" else None,
    )
    with pytest.raises(ToolError):
        await protocols.call(
            "http", {"endpoint": "https://peer.test/business"}, _authentication=auth
        )
    assert calls == [] and resolutions == [] and active_projection_guard() is None


def test_short_credential_cannot_reappear_in_the_redaction_marker_or_conflicting_keys():
    guard = LiteralCredentialGuard()
    guard.capture(["REDACTED"])
    assert "REDACTED" not in guard.text("REDACTED") and b"REDACTED" not in guard.raw(b"REDACTED")
    another = LiteralCredentialGuard()
    another.capture(["private-credential"])
    with pytest.raises(ToolError):
        another.project({"private-credential": 1, "[REDACTED]": 2})
    assert "private-credential" not in repr(another)


def test_guard_finite_bounds_and_exception_exit_do_not_leak_into_next_call():
    with pytest.raises(ToolError):
        with credential_scope() as guard:
            guard.capture(["x" * 65_537])
    assert active_projection_guard() is None
    with pytest.raises(RuntimeError):
        with credential_scope() as guard:
            guard.capture(["controlled-credential"])
            raise RuntimeError("controlled interruption")
    assert active_projection_guard() is None and not guard.contains_literal("controlled-credential")


@pytest.mark.asyncio
async def test_actual_common_sandbox_child_stdout_and_stderr_guard_resolved_original_environment(
    tmp_path, monkeypatch
):
    secret = "private-child-environment-credential-531"
    monkeypatch.setenv("OWA_APPROVED_OUTPUT_SECRET", secret)
    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "workspace_root": str(tmp_path / "workspace"),
            "executable_search_path": str(Path(sys.executable).parent),
            "secret_environment": ["OWA_APPROVED_OUTPUT_SECRET"],
            "inherited_environment": ["SYSTEMROOT"] if os.name == "nt" else [],
            "file_size_bytes": None if os.name == "nt" else 65536,
            "process_count": None
            if os.name == "posix" and getattr(os, "getuid", lambda: 0)() == 0
            else 64,
            "timeout_seconds": 8,
        }
    )
    manager = SandboxManager(InternalSandboxBackend(config))
    request = SandboxExecutionRequest(
        "guarded-real-child",
        "script",
        script_language="python",
        script_code=(
            "import os,sys,json; print(json.dumps({'pid':os.getpid(),"
            "'value':os.environ['APPROVED']})); "
            "print(os.environ['APPROVED'],file=sys.stderr)"
        ),
        environment=(("APPROVED", SandboxSecretReference("OWA_APPROVED_OUTPUT_SECRET")),),
    )
    try:
        async with asyncio.timeout(10):
            result = await manager.execute(request)
        value = json.loads(result.stdout)
        assert value["pid"] != os.getpid() and value["pid"] > 0 and value["value"] == "[REDACTED]"
        assert result.stderr == "[REDACTED]\n" and result.exit_code == 0
        assert secret not in json.dumps(asdict(result)) and secret not in repr(result)
        assert manager.backend._active == {}
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_unapproved_sandbox_secret_fails_before_any_process_launch(tmp_path, monkeypatch):
    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "workspace_root": str(tmp_path / "workspace"),
            "executable_search_path": str(Path(sys.executable).parent),
            "file_size_bytes": None if os.name == "nt" else 65536,
            "process_count": None
            if os.name == "posix" and getattr(os, "getuid", lambda: 0)() == 0
            else 64,
        }
    )
    backend = InternalSandboxBackend(config)
    calls = []

    async def launch(*args, **kwargs):
        calls.append(args)
        pytest.fail("unapproved credential caused a process launch")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    with pytest.raises(SandboxPolicyError):
        await SandboxManager(backend).execute(
            SandboxExecutionRequest(
                "denied-child",
                "script",
                script_language="python",
                script_code="pass",
                environment=(("APPROVED", SandboxSecretReference("UNAPPROVED_REFERENCE")),),
            )
        )
    assert calls == [] and backend._active == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "secret", ['private-quoted-"credential-921', "private-backslash-\\credential-285"]
)
async def test_raw_and_error_json_escaping_cannot_hide_actual_api_key(monkeypatch, secret):
    auth, _ = authentication(monkeypatch, "api_key", value=secret)
    status = 200

    def dispatch(request):
        assert request.headers["X-Private-Key"] == secret
        return httpx.Response(status, json={"echo": secret, "safe": None})

    client = HttpClient(authentication=auth, transport=httpx.MockTransport(dispatch))
    result = await client.request("GET", "https://peer.test/business", output="raw")
    assert json.loads(base64.b64decode(result)) == {"echo": "[REDACTED]", "safe": None}
    status = 403
    with pytest.raises(ToolError) as error:
        await client.request("GET", "https://peer.test/business")
    assert secret not in json.loads(error.value.details["detail"])["echo"]


@pytest.mark.asyncio
async def test_actual_script_stdin_stdout_and_independent_ledger_preserve_unicode(tmp_path):
    ledger = tmp_path / "actual-child-unicode.json"
    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "workspace_root": str(tmp_path / "workspace"),
            "executable_search_path": str(Path(sys.executable).parent),
            "inherited_environment": ["SYSTEMROOT"] if os.name == "nt" else [],
            "file_size_bytes": None if os.name == "nt" else 65536,
            "process_count": None
            if os.name == "posix" and getattr(os, "getuid", lambda: 0)() == 0
            else 64,
            "timeout_seconds": 8,
        }
    )
    manager = SandboxManager(InternalSandboxBackend(config))
    original = {"original": "π – العربية 中文 🧪", "null": None, "array": ["é", 1]}
    code = (
        "import sys,json,os; from pathlib import Path; value=json.load(sys.stdin); "
        "Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'input':value},"
        "ensure_ascii=False),encoding='utf-8'); "
        "print(json.dumps(value,ensure_ascii=False))"
    )
    request = SandboxExecutionRequest(
        "unicode-original-input",
        "script",
        script_language="python",
        script_code=code,
        stdin=json.dumps(original, ensure_ascii=False),
        arguments=(str(ledger),),
        environment=(("PYTHONIOENCODING", "cp1252"),),
    )
    try:
        async with asyncio.timeout(10):
            result = await manager.execute(request)
        business = json.loads(ledger.read_text(encoding="utf-8"))
        assert business["pid"] != os.getpid() and business["input"] == original
        assert (
            json.loads(result.stdout) == original and result.stderr == "" and result.exit_code == 0
        )
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "controller_error", "transport_error"])
async def test_docker_controller_guard_binds_actual_env_and_controller_headers(
    monkeypatch, outcome
):
    original = "actual-approved-docker-secret-628"
    controller_token = "actual-local-controller-binding-837"
    monkeypatch.setenv("OWA_DOCKER_GUARD", original)
    requests = []

    def controller(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert payload["environment"] == {"APPROVED": original}
        assert request.headers["authorization"] == "Bearer " + controller_token
        # Re-resolution would pick this changed value and leak the actual one.
        monkeypatch.setenv("OWA_DOCKER_GUARD", "changed-after-dispatch-value")
        if outcome == "transport_error":
            raise httpx.ConnectError(original + controller_token, request=request)
        if outcome == "controller_error":
            return httpx.Response(400, json={"error": {"code": original + controller_token}})
        return httpx.Response(
            200,
            json={
                "exit_code": 0,
                "stdout": original + controller_token,
                "stderr": original,
                "duration": 0.25,
            },
        )

    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "backend": "docker",
            "secret_environment": ["OWA_DOCKER_GUARD"],
            "docker": {"allowed_images": ["trusted-image@sha256:" + "a" * 64]},
        }
    )
    backend = DockerSandboxBackend(config, transport=httpx.MockTransport(controller))
    backend._client.headers["Authorization"] = "Bearer " + controller_token
    request = SandboxExecutionRequest(
        "controlled-docker-output",
        "container",
        image=config.docker.allowed_images[0],
        environment=(("APPROVED", SandboxSecretReference("OWA_DOCKER_GUARD")),),
    )
    try:
        if outcome == "success":
            result = await backend.execute(request)
            assert result.exit_code == 0 and result.duration == 0.25
            assert original not in result.stdout + result.stderr
            assert controller_token not in result.stdout + result.stderr
            assert result.stdout == "[REDACTED][REDACTED]" and result.stderr == "[REDACTED]"
        else:
            from open_workflow_agent.errors import SandboxProcessError

            with pytest.raises(SandboxProcessError) as error:
                await backend.execute(request)
            assert original not in json.dumps(error.value.as_dict())
            assert controller_token not in json.dumps(error.value.as_dict())
            assert error.value.__suppress_context__ is True
        assert len(requests) == 1 and backend._active == set()
        assert active_projection_guard() is None
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_docker_unapproved_secret_fails_before_controller_io(monkeypatch):
    calls = []

    def controller(request):
        calls.append(request)
        pytest.fail("unapproved Docker credential reached controller")

    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "backend": "docker",
            "docker": {"allowed_images": ["trusted-image@sha256:" + "a" * 64]},
        }
    )
    backend = DockerSandboxBackend(config, transport=httpx.MockTransport(controller))
    try:
        with pytest.raises(SandboxPolicyError):
            await backend.execute(
                SandboxExecutionRequest(
                    "denied-docker-output",
                    "container",
                    image=config.docker.allowed_images[0],
                    environment=(("DENIED", SandboxSecretReference("UNAPPROVED_RUNTIME_REF")),),
                )
            )
        assert calls == [] and backend._active == set() and active_projection_guard() is None
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "mcp"])
async def test_prepared_business_permission_is_checked_before_any_transport_or_credentials(
    protocol,
):
    calls = []

    async def authorize(protocol_name, endpoint, payload):
        calls.append((protocol_name, endpoint, payload))
        raise ToolError("controlled policy denial")

    def transport(request):
        pytest.fail("pure business authorization opened a transport")

    services = ProtocolServices(
        http=HttpClient(transport=httpx.MockTransport(transport)), authorize_destination=authorize
    )
    payload = (
        {"endpoint": "https://allowed.test/business"}
        if protocol == "http"
        else {
            "method": "tools/list",
            "transport": {"http": {"endpoint": "https://allowed.test/business"}},
        }
    )
    with pytest.raises(ToolError, match="controlled policy denial"):
        await services.authorize_prepared_call(protocol, payload)
    assert len(calls) == 1 and calls[0][0:2] == (protocol, "https://allowed.test/business")
    assert active_projection_guard() is None


@pytest.mark.asyncio
async def test_metadata_authorization_scope_has_exact_endpoint_and_resets_after_denial():
    calls = []

    async def authorize(protocol, endpoint, _payload):
        calls.append((protocol, endpoint))
        raise ToolError("metadata policy denial")

    def transport(request):
        return httpx.Response(200, json={"safe": None})

    services = ProtocolServices(
        http=HttpClient(transport=httpx.MockTransport(transport)), authorize_destination=authorize
    )
    with services.authorization_scope("a2a"):
        with pytest.raises(ToolError, match="metadata policy denial"):
            await services.http.request("GET", "https://metadata.test/card")
    assert calls == [("a2a", "https://metadata.test/card")]
    assert await services.http.request("GET", "https://metadata.test/card") == {"safe": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "controller_error", "transport_error"])
async def test_kubernetes_pod_secret_output_is_suppressed_without_reading_secret_values(
    monkeypatch, outcome
):
    secret = "controller-only-pod-secret-493"
    token = "actual-kube-controller-credential-826"
    calls = []

    def controller(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer " + token
        payload = json.loads(request.content)
        assert payload["environment"] == {"POD_SECRET": {"secret_ref": "APPROVED_KEY"}}
        assert secret not in request.content.decode() and not request.url.path.startswith("/api/")
        if outcome == "controller_error":
            return httpx.Response(400, json={"error": {"code": secret, "message": secret}})
        if outcome == "transport_error":
            raise httpx.ConnectError(secret + token, request=request)
        return httpx.Response(
            200,
            json={"stdout": secret + token, "stderr": secret, "exit_code": 17, "duration": 0.125},
        )

    config = SandboxConfig.model_validate(
        {
            "enabled": True,
            "backend": "kubernetes",
            "kubernetes": {
                "allowed_images": ["trusted-pod@sha256:" + "b" * 64],
                "secret_name": "trusted-secret",
                "secret_keys": ["APPROVED_KEY"],
                "network_policy_enforced": True,
                "process_limit_enforced": True,
            },
        }
    )
    backend = KubernetesSandboxBackend(config, transport=httpx.MockTransport(controller))
    backend._client.headers["Authorization"] = "Bearer " + token
    request = SandboxExecutionRequest(
        "pod-secret-completion",
        "container",
        image=config.kubernetes.allowed_images[0],
        environment=(("POD_SECRET", SandboxSecretReference("APPROVED_KEY")),),
    )

    def no_secret_read(name, *args):
        if name == "APPROVED_KEY":
            pytest.fail("runtime attempted to resolve pod Secret value")
        return None

    monkeypatch.setattr(os, "getenv", no_secret_read)
    try:
        if outcome == "success":
            result = await backend.execute(request)
            assert (
                result.stdout == result.stderr == ""
                and result.exit_code == 17
                and result.duration == 0.125
            )
            # All workflow projections retain completion truth and expose no pod log.
            for mode in ("stdout", "stderr", "all", "none", "code"):
                value = (
                    asdict(result)
                    if mode == "all"
                    else (
                        None
                        if mode == "none"
                        else getattr(result, "exit_code" if mode == "code" else mode)
                    )
                )
                assert secret not in json.dumps(value) and token not in json.dumps(value)
            assert backend.capabilities()["container"]["secretEnvironmentOutput"] == "suppressed"
        else:
            from open_workflow_agent.errors import SandboxProcessError

            with pytest.raises(SandboxProcessError) as error:
                await backend.execute(request)
            assert secret not in json.dumps(error.value.as_dict()) and token not in json.dumps(
                error.value.as_dict()
            )
            assert error.value.__suppress_context__ is True
        assert len(calls) == 1 and backend._active == set() and active_projection_guard() is None
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_openapi_preintent_authorizes_exact_encoded_path_and_rejects_invalid_parameters():
    from test_openapi_client import selection

    calls = []

    async def authorize(protocol, endpoint, _payload):
        calls.append((protocol, endpoint))

    def transport(request):
        pytest.fail("preintent OpenAPI preparation performed I/O")

    services = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(transport)), authorize_destination=authorize
    )
    selected = selection()
    await services.authorize_prepared_call(
        "openapi", {"parameters": {"id": "a b/π"}}, _openapi_selection=selected
    )
    assert calls == [("openapi", "https://api.test/v1/items/a%20b%2F%CF%80")]
    with pytest.raises(ToolError):
        await services.authorize_prepared_call(
            "openapi", {"parameters": {}}, _openapi_selection=selected
        )
    assert len(calls) == 1
