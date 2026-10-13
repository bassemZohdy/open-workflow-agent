"""Actual named ingress and common ownership; no synthetic native qualification."""

from __future__ import annotations

import json
import os
from uuid import uuid4

import httpx
import pytest
from fastapi import Request
from open_workflow_agent.a2a import _A2APrincipalMiddleware, _authenticate
from open_workflow_agent.api import ApiAuthenticationMiddleware, create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import A2AConfig, RuntimeConfig
from open_workflow_agent.errors import ApprovalAuthorizationError
from open_workflow_agent.execution_authority import (
    ExecutionAuthority,
    authority_scope,
    current_authority,
)
from open_workflow_agent.security import SecurityConfig, static_principal
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from test_protected_approvals import seed, snapshot


def named_config(monkeypatch, kind="bearer"):
    monkeypatch.setenv("OWA_INGRESS_CONTROLLED", "controlled-static-token-815")
    profile = {
        "type": kind,
        "principal": "actual-caller",
        "roles": ["operator"],
        "scopes": ["read"],
        "audience": ["owa"],
    }
    profile.update(
        {"token": {"from_env": "OWA_INGRESS_CONTROLLED"}}
        if kind == "bearer"
        else {"header": "X-Private-Key", "key": {"from_env": "OWA_INGRESS_CONTROLLED"}}
    )
    return SecurityConfig.model_validate({"profiles": {"ingress": profile}})


async def request_middleware(middleware, raw_headers, *, path="/v1/invoke"):
    messages = []

    async def receive():
        return {
            "type": "http.request",
            "body": b'{"user_id":"forged-admin","input":{"roles":["admin"]}}',
            "more_body": False,
        }

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": raw_headers,
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 1),
        "scheme": "http",
        "http_version": "1.1",
        "owa.principal": "forged-admin",
    }
    await middleware(scope, receive, send)
    return messages, scope


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bearer", "api_key"])
@pytest.mark.parametrize(
    "invalid", ["absent", "bare_or_scheme", "duplicate", "unicode", "control", "oversize", "wrong"]
)
async def test_strict_ingress_rejects_ambiguous_headers_before_any_request_execution(
    monkeypatch, kind, invalid
):
    security = named_config(monkeypatch, kind)
    name = b"authorization" if kind == "bearer" else b"x-private-key"
    valid = (
        b"Bearer controlled-static-token-815"
        if kind == "bearer"
        else b"controlled-static-token-815"
    )
    raw = [(name, valid)]
    if invalid == "absent":
        raw = []
    elif invalid == "bare_or_scheme":
        raw = [
            (
                name,
                b"controlled-static-token-815"
                if kind == "bearer"
                else b"Bearer controlled-static-token-815",
            )
        ]
    elif invalid == "duplicate":
        raw.append((name.upper(), valid))
    elif invalid == "unicode":
        raw = [(name, b"Bearer " + b"\xff" if kind == "bearer" else b"\xff")]
    elif invalid == "control":
        raw = [(name, valid + b"\r\n")]
    elif invalid == "oversize":
        raw = [(name, b"Bearer " + b"x" * 8193)]
    else:
        raw = [(name, b"Bearer wrong" if kind == "bearer" else b"wrong")]
    entered = []

    async def app(scope, receive, send):
        entered.append(scope)

    messages, _ = await request_middleware(
        ApiAuthenticationMiddleware(app, security=security, profile_name="ingress"), raw
    )
    assert entered == [] and messages[0]["status"] == (401 if invalid == "absent" else 403)
    assert "controlled-static-token-815" not in repr(messages) and current_authority() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bearer", "api_key"])
async def test_actual_ingress_principal_claims_survive_without_caller_label_authority(
    monkeypatch, kind
):
    security = named_config(monkeypatch, kind)
    observed = []

    async def app(scope, receive, send):
        observed.append((current_authority(), scope["owa.principal"], await receive()))

    header = (
        (b"authorization", b"Bearer controlled-static-token-815")
        if kind == "bearer"
        else (b"x-private-key", b"controlled-static-token-815")
    )
    await request_middleware(
        ApiAuthenticationMiddleware(app, security=security, profile_name="ingress"), [header]
    )
    authority, principal, body = observed[0]
    assert isinstance(authority, ExecutionAuthority) and principal == "actual-caller"
    assert authority.principal.roles == frozenset(
        {"operator"}
    ) and authority.principal.scopes == frozenset({"read"})
    assert (
        authority.principal.audience == frozenset({"owa"})
        and authority.security_profile == "ingress"
    )
    assert b"forged-admin" in body["body"] and "controlled-static-token-815" not in json.dumps(
        authority.as_value()
    )
    assert current_authority() is None


@pytest.mark.asyncio
async def test_a2a_ingress_uses_shared_parser_and_restores_full_actual_principal(monkeypatch):
    security = named_config(monkeypatch)
    config = A2AConfig(enabled=True, security_profile="ingress")
    observed = []

    async def app(scope, receive, send):
        principal = _authenticate(config, security, Request(scope, receive))
        if principal is not None:
            observed.append((principal, current_authority()))

    middleware = _A2APrincipalMiddleware(app, config=config, security=security)
    await request_middleware(
        middleware, [(b"authorization", b"controlled-static-token-815")], path=config.path
    )
    await request_middleware(middleware, [(b"authorization", b"Bearer \xff")], path=config.path)
    assert observed == []
    await request_middleware(
        middleware, [(b"authorization", b"Bearer controlled-static-token-815")], path=config.path
    )
    assert observed[0][0] == observed[0][1].principal and observed[0][0].roles == frozenset(
        {"operator"}
    )
    assert current_authority() is None


@pytest.fixture(params=["sqlite", "postgresql"])
def api_storage(tmp_path, monkeypatch, request):
    backend = request.param
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("actual PostgreSQL is required")
    marker = uuid4().hex
    for name in ("a", "b"):
        monkeypatch.setenv("OWA_API_AUTHORITY_" + name.upper(), "controlled-" + name + "-token")
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "persistence": {"datasource": datasource},
            "server": {"api_security_profile": "b"},
            "security": {
                "profiles": {
                    name: {
                        "type": "bearer",
                        "principal": name + "-" + marker,
                        "roles": ["reader"],
                        "token": {"from_env": "OWA_API_AUTHORITY_" + name.upper()},
                    }
                    for name in ("a", "b")
                }
            },
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else tmp_path
    )
    assert (
        services.effect_journal.connection.kind == services.invocations.connection.kind == backend
    )
    yield services, config
    services.close()


@pytest.mark.asyncio
async def test_schedule_foreign_and_missing_object_are_indistinguishable_without_mutation(
    api_storage,
):
    services, config = api_storage
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "identity",
                "name": "schedule",
                "version": "1.0.0",
            },
            "schedule": {"after": {"seconds": 60}},
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )
    principal = static_principal(config.security.profile("a"))
    with authority_scope(ExecutionAuthority.capture(principal, security_profile="a")):
        record = services.schedules.create(
            plan,
            {"original": None},
            owner_principal=principal.identity,
            event_scope=principal.identity,
        )
    before = services.schedules.get(record.schedule_id).as_dict()
    app = create_app(config=config, services=services)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method, suffix in (("GET", ""), ("POST", "/cancel")):
            foreign = await client.request(
                method,
                "/v1/schedules/" + record.schedule_id + suffix,
                headers={"Authorization": "Bearer controlled-b-token"},
            )
            missing = await client.request(
                method,
                "/v1/schedules/" + str(uuid4()) + suffix,
                headers={"Authorization": "Bearer controlled-b-token"},
            )
            assert foreign.status_code == missing.status_code == 404
            assert (
                foreign.json()["error"]["code"]
                == missing.json()["error"]["code"]
                == "schedule_not_found"
            )
            assert (
                services.schedules.get(record.schedule_id).as_dict() == before
                and services.model.calls == []
            )
    # Separate trusted API binding to the same actual common store: this is
    # an ownership unit witness, not an SDK/native deployment qualification.
    owner_config = config.model_copy(deep=True)
    owner_config.server.api_security_profile = "a"
    owner_app = create_app(config=owner_config, services=services)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=owner_app), base_url="http://test"
    ) as client:
        permitted = await client.get(
            "/v1/schedules/" + record.schedule_id,
            headers={"Authorization": "Bearer controlled-a-token"},
        )
        assert (
            permitted.status_code == 200 and permitted.json()["schedule_id"] == record.schedule_id
        )
    assert services.model.calls == []


@pytest.mark.asyncio
async def test_activated_api_policy_denies_admin_and_invocation_before_state_or_model(
    api_storage, monkeypatch
):
    services, config = api_storage
    # Activate a finite deny-by-default current policy on the actual composed
    # configuration; credentials still establish the same named static caller.
    config.execution_authorization.enabled = True
    config.execution_authorization.id = "deny-current"
    calls = []

    def reload():
        calls.append("reload")
        pytest.fail("denied administration performed a reload")

    monkeypatch.setattr(services.knowledge, "reload", reload)
    principal = static_principal(config.security.profile("b")).identity
    before = services.invocations.connection.execute(
        "SELECT invocation_id,payload,status FROM invocations WHERE payload LIKE ?",
        ("%" + principal + "%",),
    ).fetchall()
    app = create_app(config=config, services=services)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path, body in (
            ("/v1/admin/knowledge/reload", {}),
            ("/v1/invoke", {"user_id": "forged-admin", "input": {"roles": ["admin"]}}),
        ):
            denied = await client.post(
                path, headers={"Authorization": "Bearer controlled-b-token"}, json=body
            )
            assert denied.status_code == 403 and "controlled-b-token" not in denied.text
    assert (
        services.invocations.connection.execute(
            "SELECT invocation_id,payload,status FROM invocations WHERE payload LIKE ?",
            ("%" + principal + "%",),
        ).fetchall()
        == before
    )
    assert calls == [] and services.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GetTask", "CancelTask", "SubscribeToTask"])
async def test_activated_a2a_task_policy_denies_before_state_control_or_stream(api_storage, method):
    services, config = api_storage
    config.execution_authorization.enabled = True
    config.execution_authorization.id = "deny-a2a-task-current"
    config.a2a.enabled = True
    config.a2a.security_profile = "b"
    principal = static_principal(config.security.profile("b"))
    handle = services.invocations.create(
        engine="adk",
        session_id=None,
        user_id="untrusted-correlation-label",
        workflow_name="identity-task",
        workflow_version="1.0.0",
        workflow_fingerprint="f" * 64,
        owner_principal=principal.identity,
    )
    before = services.invocations.connection.execute(
        "SELECT payload,status FROM invocations WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()
    app = create_app(config=config, services=services)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.post(
            config.a2a.path,
            headers={"Authorization": "Bearer controlled-b-token", "A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": "denied-operation",
                "method": method,
                "params": {"id": handle.invocation_id},
            },
        )
    assert denied.status_code == 403 and denied.json()["id"] == "denied-operation"
    assert "controlled-b-token" not in denied.text
    assert (
        services.invocations.connection.execute(
            "SELECT payload,status FROM invocations WHERE invocation_id=?",
            (handle.invocation_id,),
        ).fetchone()
        == before
    )
    assert services.model.calls == [] and current_authority() is None


@pytest.mark.asyncio
async def test_legacy_approval_malformed_header_is_safe_without_primary_ingress(
    protected_runtime, monkeypatch
):
    services, config, _ = protected_runtime
    subject, _, _ = await seed(protected_runtime)
    legacy_config = config.model_copy(deep=True)
    legacy_config.server.api_security_profile = None
    app = create_app(config=legacy_config, services=services)
    before = snapshot(services, subject)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for token in (chr(0x03C0), "configured-token\r\n", " configured-token "):
            with monkeypatch.context() as patch:
                patch.setenv("OWA_APPROVAL_API", token)
                denied = await client.post(
                    f"/v1/approvals/{subject}/decision",
                    headers=[
                        (b"authorization", b"Bearer " + token.encode("utf-8")),
                        (b"x-operator-id", b"legacy-caller-label"),
                        (b"idempotency-key", uuid4().hex.encode()),
                    ],
                    json={"decision": "approved", "value": None, "revision": 1},
                )
            assert denied.status_code == 403 and "configured-token" not in denied.text
            assert snapshot(services, subject) == before
        duplicate = await client.post(
            f"/v1/approvals/{subject}/decision",
            headers=[
                (b"authorization", b"Bearer api-controlled-token"),
                (b"Authorization", b"Bearer api-controlled-token"),
                (b"x-operator-id", b"legacy-caller-label"),
                (b"idempotency-key", uuid4().hex.encode()),
            ],
            json={"decision": "approved", "value": None, "revision": 1},
        )
        assert duplicate.status_code == 403 and snapshot(services, subject) == before
    # Legacy correlation label remains compatibility data, not a protected actor.
    assert (
        services.approvals.authorize("bearer api-controlled-token", "legacy-caller-label")
        == "legacy-caller-label"
    )


@pytest.mark.asyncio
async def test_protected_actor_malformed_even_matching_deployed_token_cannot_mutate(
    protected_runtime, monkeypatch
):
    services, config, principals = protected_runtime
    subject, _, _ = await seed(protected_runtime)
    app = create_app(config=config, services=services)
    before = snapshot(services, subject)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for token in (chr(0x03C0), "configured-token\n", " configured-token "):
            with monkeypatch.context() as patch:
                patch.setenv("OWA_APPROVAL_A", token)
                denied = await client.post(
                    f"/v1/approvals/{subject}/decision",
                    headers=[
                        (b"authorization", b"Bearer api-controlled-token"),
                        (b"x-approval-authorization", b"Bearer " + token.encode("utf-8")),
                        (b"idempotency-key", uuid4().hex.encode()),
                    ],
                    json={"decision": "approved", "value": None, "revision": 1},
                )
                with pytest.raises(ApprovalAuthorizationError):
                    services.approvals.authorize_actor("Bearer " + token)
            assert denied.status_code == 403 and "configured-token" not in denied.text
            assert snapshot(services, subject) == before
    actor = services.approvals.authorize_actor("bEaReR a-controlled-token")
    assert actor.identity == principals["a"] and actor != current_authority()


@pytest.mark.asyncio
async def test_duplicate_secondary_actor_headers_fail_all_five_routes_before_mutation(
    protected_runtime,
):
    services, config, _ = protected_runtime
    subject, _, _ = await seed(protected_runtime)
    app = create_app(config=config, services=services)
    before = snapshot(services, subject)
    routes = [
        ("GET", "/v1/approvals", None),
        ("GET", f"/v1/approvals/{subject}", None),
        (
            "POST",
            f"/v1/approvals/{subject}/decision",
            {"decision": "approved", "value": None, "revision": 1},
        ),
        (
            "POST",
            f"/v1/approvals/{subject}/reassign",
            {"assignee": "untrusted-target", "revision": 1},
        ),
        (
            "POST",
            f"/v1/approvals/{subject}/escalate",
            {"assignee": "untrusted-target", "revision": 1},
        ),
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method, path, body in routes:
            denied = await client.request(
                method,
                path,
                json=body,
                headers=[
                    (b"authorization", b"Bearer api-controlled-token"),
                    (b"x-approval-authorization", b"Bearer a-controlled-token"),
                    (b"X-Approval-Authorization", b"Bearer b-controlled-token"),
                    (b"idempotency-key", uuid4().hex.encode()),
                ],
            )
            assert denied.status_code == 403 and "controlled-token" not in denied.text
            assert snapshot(services, subject) == before


@pytest.mark.asyncio
async def test_duplicate_matching_actor_credentials_never_select_first_named_actor(
    protected_runtime, monkeypatch
):
    services, config, _ = protected_runtime
    subject, _, _ = await seed(protected_runtime)
    before = snapshot(services, subject)
    monkeypatch.setenv("OWA_APPROVAL_B", "a-controlled-token")
    with pytest.raises(ApprovalAuthorizationError):
        services.approvals.authorize_actor("Bearer a-controlled-token")
    app = create_app(config=config, services=services)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.post(
            f"/v1/approvals/{subject}/decision",
            headers={
                "Authorization": "Bearer api-controlled-token",
                "X-Approval-Authorization": "Bearer a-controlled-token",
                "Idempotency-Key": uuid4().hex,
            },
            json={"decision": "approved", "value": None, "revision": 1},
        )
    assert denied.status_code == 403 and snapshot(services, subject) == before
