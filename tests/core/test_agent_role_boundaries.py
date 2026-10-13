"""Bounded role/caller intersection on real common stores; no native claim."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.execution_authority import (
    ExecutionAuthority,
    authority_scope,
    current_authority,
)
from open_workflow_agent.openapi_client import OpenApiDescription
from open_workflow_agent.recovery_definitions import PROFILE
from open_workflow_agent.security import static_principal
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import compile_workflow

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


@pytest.fixture
def explicit_workflow_delivery(monkeypatch):
    """This common-unit module drives its own invokes and approval resumes.

    Keep unrelated PostgreSQL timer rows out of its model/cardinality oracle;
    this fixture does not qualify automatic timer delivery.
    """

    async def no_automatic_delivery(_dispatcher):
        return None

    monkeypatch.setattr(TimerDispatcher, "start", no_automatic_delivery)


@dataclass(repr=False)
class RecordingRoleModel:
    responses: list[Any] = field(default_factory=lambda: [{"answer": "fixed-role-answer"}])
    calls: list[Any] = field(default_factory=list)
    options: list[Any] = field(default_factory=list)
    principals: list[Any] = field(default_factory=list)

    async def complete(self, prompt: Any, *, options: dict[str, Any] | None = None) -> Any:
        self.calls.append(copy.deepcopy(prompt))
        self.options.append(copy.deepcopy(options))
        self.principals.append(current_authority())
        if not self.responses:
            pytest.fail("role exceeded its declared model cardinality")
        value = self.responses.pop(0)
        return value(prompt, options) if callable(value) else copy.deepcopy(value)


@dataclass(repr=False)
class RoleRuntime:
    services: RuntimeServices
    engine: PortableWorkflowEngine
    models: dict[str, RecordingRoleModel]
    authority: ExecutionAuthority
    business: list[Any]

    async def invoke(self, payload: Any, *, function: str = "agent-writer:1.0.0@default"):
        source = {
            "document": {
                "dsl": "1.0.3",
                "namespace": "role-boundaries",
                "name": "case-" + uuid4().hex,
                "version": "1.0.0",
            },
            "do": [{"action": {"call": function, "with": payload}}],
        }
        plan = compile_workflow(source, enabled_function_calls=self.services.role_function_calls)
        handle = self.services.invocations.create(
            engine="reference",
            session_id=None,
            user_id="untrusted-correlation-label",
            owner_principal=self.authority.principal.identity,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        self.services.execution_authorizations.reserve_invocation(
            handle, self.authority, input_data={"original": None}
        )
        with authority_scope(self.authority):
            async with asyncio.timeout(10):
                result = await self.engine.invoke(plan, handle, {"original": None})
        return result, handle, plan


def _deployed_tool(name):
    document = {
        "openapi": "3.0.3",
        "info": {"title": name, "version": "1.0.0"},
        "servers": [{"url": "https://tool.test"}],
        "paths": {
            "/" + name: {
                "get": {
                    "operationId": "tool-" + name,
                    "responses": {"200": {"description": "Known tool response"}},
                }
            }
        },
    }
    description = OpenApiDescription.capture(
        "https://tool.test/" + name + "-openapi.json", document
    )
    return {
        "name": name,
        "type": "openapi",
        "endpoint": "https://tool.test/" + name,
        "options": {"operationId": "tool-" + name},
        "openapi_binding": {
            "id": "role-tool-" + name,
            "version": "1.0.0",
            "document_uri": description.uri,
            "document": document,
            "digest": description.digest,
        },
    }


@pytest.fixture(params=["sqlite", "postgresql"])
async def role_runtime(tmp_path, monkeypatch, request):
    backend = request.param
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("actual PostgreSQL is required for role boundary proof")
    opened = []

    async def make(
        *, role_changes=None, caller_rules=None, responses=None, credentials=None, provider=False
    ):
        marker = uuid4().hex
        variable = "OWA_ROLE_CALLER_" + marker.upper()
        monkeypatch.setenv(variable, "controlled-role-caller-token")
        role = {
            "id": "writer-binding",
            "version": "1.0.0",
            "model": {"provider": "fake", "name": "fake/writer"},
            "instruction": "trusted-writer-instruction",
            "tools": ["safe"],
            "max_rounds": 2,
            "max_model_calls": 2,
            "max_tool_calls": 2,
            "max_child_invocations": 0,
            "max_output_bytes": 1048576,
            "policy": {"rules": [{"actions": ["*"], "resources": ["*"], "roles": ["operator"]}]},
        }
        role.update(copy.deepcopy(role_changes or {}))
        if credentials is not None:
            role["model"]["credentials"] = credentials
        if provider:
            role["model"].update({"provider": "litellm", "name": "provider/guarded"})
        config = RuntimeConfig.model_validate(
            {
                "model": {"provider": "fake"},
                "persistence": {"datasource": datasource},
                "security": {
                    "profiles": {
                        "caller": {
                            "type": "bearer",
                            "principal": "caller-" + marker,
                            "roles": ["operator"],
                            "scopes": ["execute"],
                            "audience": ["runtime"],
                            "token": {"from_env": variable},
                        }
                    }
                },
                "execution_authorization": {
                    "enabled": True,
                    "id": "original-caller-policy",
                    "policy": {
                        "rules": caller_rules
                        if caller_rules is not None
                        else [{"actions": ["*"], "resources": ["*"], "roles": ["operator"]}]
                    },
                },
                "agent": {
                    "tools": ["safe", "admin"],
                    "roles": {
                        "writer": role,
                        "approver-action": {
                            **copy.deepcopy(role),
                            "id": "approver-action-binding",
                        },
                    },
                },
                "tools": [_deployed_tool(name) for name in ("safe", "admin")],
                "approvals": {
                    "enabled": True,
                    "protected": True,
                    "operator_security_profile": "caller",
                    "actor_security_profiles": ["caller"],
                },
            }
        )
        models = {
            "writer": RecordingRoleModel(
                copy.deepcopy(responses)
                if responses is not None
                else [{"answer": "fixed-role-answer"}]
            ),
            "approver-action": RecordingRoleModel(),
        }
        services = RuntimeServices(
            config,
            model=FakeModel(),
            role_models={} if provider else models,
            database_root=None if datasource else tmp_path / marker,
        )
        assert (
            services.effect_journal.connection.kind
            == services.invocations.connection.kind
            == backend
        )
        # Deliberate common-store seam only. Native SDK/code qualification is
        # independently required by the shared matrix; these unit proofs never
        # substitute a fake profile for native adapter evidence.
        services.recovery_definitions.profile_provider = lambda engine: {
            "profile": PROFILE,
            "engine": engine,
            "backend": backend,
            "continuation_supported": True,
            "qualification": "test-only/common-role-boundary",
            "policy": {"agent_roles": services.role_bindings.snapshot()},
        }
        business = []

        def receiver(wire):
            business.append((str(wire.url), wire.method, wire.content))
            return httpx.Response(200, json={"tool_result": None})

        services.protocols.http.transport = httpx.MockTransport(receiver)
        engine = PortableWorkflowEngine()
        await engine.initialize(services)
        principal = static_principal(config.security.profile("caller"))
        runtime = RoleRuntime(
            services,
            engine,
            models,
            ExecutionAuthority.capture(principal, security_profile="caller"),
            business,
        )
        opened.append(runtime)
        return runtime

    try:
        yield make
    finally:
        for runtime in reversed(opened):
            await runtime.engine.shutdown()
            runtime.services.close()


@pytest.mark.asyncio
async def test_role_allow_cannot_grant_original_caller_a_denied_model(role_runtime):
    runtime = await role_runtime(
        caller_rules=[{"actions": ["catalog.call"], "resources": ["*"], "roles": ["operator"]}]
    )
    result, handle, _ = await runtime.invoke({"input": "ordinary-user-data"})
    assert (
        result.status == "faulted"
        and runtime.models["writer"].calls == []
        and runtime.business == []
    )
    original = runtime.services.execution_authorizations.verify_invocation(handle)
    assert original == runtime.authority and original.principal.roles == frozenset({"operator"})
    assert runtime.services.model.calls == [] and current_authority() is None


@pytest.mark.asyncio
async def test_role_tool_allow_cannot_grant_original_caller_denied_tool(role_runtime):
    runtime = await role_runtime(
        caller_rules=[
            {
                "actions": ["catalog.call", "model.complete"],
                "resources": ["*"],
                "roles": ["operator"],
            }
        ],
        responses=[{"tool_call": {"name": "safe", "arguments": {}}}],
    )
    result, _, _ = await runtime.invoke({"input": "user"})
    assert (
        result.status == "faulted"
        and len(runtime.models["writer"].calls) == 1
        and runtime.business == []
    )
    assert runtime.models["writer"].principals == [runtime.authority]


@pytest.mark.asyncio
async def test_role_batch_rejects_forbidden_tool_before_first_allowed_side_effect(role_runtime):
    runtime = await role_runtime(
        responses=[
            {
                "tool_calls": [
                    {"name": "safe", "arguments": {}},
                    {"name": "admin", "arguments": {"roles": ["admin"]}},
                ]
            }
        ]
    )
    result, _, _ = await runtime.invoke({"input": "caller-can-use-admin-but-this-role-cannot"})
    assert (
        result.status == "faulted"
        and len(runtime.models["writer"].calls) == 1
        and runtime.business == []
    )
    assert runtime.models["writer"].principals == [runtime.authority]


@pytest.mark.asyncio
async def test_role_full_batch_tool_budget_is_checked_before_dispatch(role_runtime):
    runtime = await role_runtime(
        role_changes={"max_tool_calls": 1},
        responses=[
            {"tool_calls": [{"name": "safe", "arguments": {}}, {"name": "safe", "arguments": {}}]}
        ],
    )
    result, _, _ = await runtime.invoke({"input": "two-requests-one-budget"})
    assert (
        result.status == "faulted"
        and len(runtime.models["writer"].calls) == 1
        and runtime.business == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["max_model_calls", "max_rounds"])
async def test_declared_role_budget_never_starts_an_extra_model_round(role_runtime, limit):
    runtime = await role_runtime(
        role_changes={limit: 1}, responses=[{"tool_call": {"name": "safe", "arguments": {}}}]
    )
    result, _, _ = await runtime.invoke({"input": "bounded-role"})
    assert (
        result.status == "faulted"
        and len(runtime.models["writer"].calls) == 1
        and len(runtime.business) == 1
    )
    assert runtime.models["writer"].principals == [runtime.authority]


@pytest.mark.asyncio
async def test_model_tool_arguments_cannot_replace_trusted_route_or_credential_profile(
    role_runtime,
):
    runtime = await role_runtime(
        responses=[
            {
                "tool_call": {
                    "name": "safe",
                    "arguments": {
                        "endpoint": "https://untrusted.test/admin",
                        "method": "DELETE",
                        "security_profile": "foreign",
                        "body": {"user_id": "administrator"},
                    },
                }
            }
        ]
    )
    result, _, _ = await runtime.invoke({"input": "malicious-tool-routing"})
    assert (
        result.status == "faulted"
        and len(runtime.models["writer"].calls) == 1
        and runtime.business == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector",
    [
        "role",
        "provider",
        "model",
        "options",
        "tools",
        "instruction",
        "credentials",
        "security_profile",
        "authority",
    ],
)
async def test_caller_role_selector_fields_never_rebind_model_or_permissions(
    role_runtime, selector
):
    runtime = await role_runtime()
    value = {"options": {"api_key": "caller-supplied-credential"}, "tools": ["admin"]}.get(
        selector, "admin"
    )
    result, _, _ = await runtime.invoke({"input": "ordinary", selector: value})
    assert (
        result.status == "faulted"
        and runtime.models["writer"].calls == []
        and runtime.business == []
    )
    assert runtime.models["approver-action"].calls == [] and runtime.services.model.calls == []


@pytest.mark.asyncio
async def test_role_output_bound_rejects_oversized_response_before_retained_output(role_runtime):
    runtime = await role_runtime(
        role_changes={"max_output_bytes": 64}, responses=[{"answer": "x" * 128}]
    )
    result, handle, _ = await runtime.invoke({"input": "bounded-output"})
    assert (
        result.status == "faulted"
        and len(runtime.models["writer"].calls) == 1
        and runtime.business == []
    )
    assert runtime.services.invocations.get(handle.invocation_id).output is None


@pytest.mark.asyncio
async def test_actual_model_credential_ref_echo_is_guarded_before_archive_receipts_and_output(
    role_runtime, monkeypatch
):
    canary = "actual-provider-spi-secret-694"
    monkeypatch.setenv("OWA_ROLE_MODEL_CANARY", canary)
    provider_calls = []

    async def completion(**options):
        assert options["api_key"] == canary and options["model"] == "provider/guarded"
        provider_calls.append((copy.deepcopy(options), current_authority()))
        content = json.dumps({"echo": options["api_key"], "safe": None})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    # Real common LiteLLM adapter credential resolution and response projection;
    # only its external provider operation is a deterministic no-paid oracle.
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=completion))
    runtime = await role_runtime(
        credentials={"api_key": {"from_env": "OWA_ROLE_MODEL_CANARY"}}, provider=True
    )
    result, handle, _ = await runtime.invoke({"input": "original"})
    assert result.status == "completed"
    assert json.loads(result.output) == {"echo": "[REDACTED]", "safe": None}
    assert len(provider_calls) == 1 and provider_calls[0][1] == runtime.authority
    connection = runtime.services.effect_journal.connection
    rows = connection.execute(
        "SELECT payload FROM workflow_effects WHERE identity IN "
        "(SELECT identity FROM workflow_effect_invocations WHERE invocation_id=?)",
        (handle.invocation_id,),
    ).fetchall()
    archived = connection.execute(
        "SELECT payload FROM recovery_invocation_definitions WHERE invocation_id=?",
        (handle.invocation_id,),
    ).fetchone()
    raw = connection.execute(
        "SELECT payload FROM invocations WHERE invocation_id=?", (handle.invocation_id,)
    ).fetchone()
    assert canary not in repr(rows) + repr(archived) + repr(raw) + repr(
        runtime.services.lifecycle_events.events
    )
    assert current_authority() is None


@pytest.mark.asyncio
async def test_terminal_role_receipt_does_not_repeat_provider_or_tool(role_runtime):
    runtime = await role_runtime(
        responses=[{"tool_call": {"name": "safe", "arguments": {}}}, {"answer": "known"}]
    )
    result, handle, plan = await runtime.invoke({"input": "original"})
    assert (
        result.status == "completed"
        and len(runtime.models["writer"].calls) == 2
        and len(runtime.business) == 1
    )
    before = (len(runtime.models["writer"].calls), copy.deepcopy(runtime.business))
    with authority_scope(runtime.authority):
        again = await runtime.engine.invoke(plan, handle, {"changed": True})
    assert (
        again.output == result.output
        and (len(runtime.models["writer"].calls), runtime.business) == before
    )


@pytest.mark.asyncio
async def test_role_policy_does_not_augment_original_principal_roles(role_runtime):
    runtime = await role_runtime(
        role_changes={
            "policy": {
                "rules": [{"actions": ["*"], "resources": ["*"], "roles": ["administrator"]}]
            }
        }
    )
    result, handle, _ = await runtime.invoke(
        {"input": {"roles": ["administrator"], "principal": "admin"}}
    )
    assert (
        result.status == "faulted"
        and runtime.models["writer"].calls == []
        and runtime.business == []
    )
    assert runtime.services.execution_authorizations.verify_invocation(handle) == runtime.authority
    assert runtime.authority.principal.roles == frozenset({"operator"})


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "rejected"])
async def test_actual_protected_approval_gate_has_zero_role_business_before_decision(
    role_runtime, decision
):
    runtime = await role_runtime()
    services = runtime.services
    source = Path(__file__).resolve().parents[2] / "resources" / "patterns" / "approval-gate.yaml"
    plan = compile_workflow(source, enabled_function_calls=services.role_function_calls)
    raw = {
        "question": "original-protected-question",
        "requestId": str(uuid4()),
        "expiresAt": (datetime.now(UTC) + timedelta(seconds=300))
        .isoformat()
        .replace("+00:00", "Z"),
        "assignee": runtime.authority.principal.identity,
    }
    handle = services.invocations.create(
        engine="reference",
        session_id=None,
        user_id="untrusted-correlation-only",
        owner_principal=runtime.authority.principal.identity,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    services.execution_authorizations.reserve_invocation(handle, runtime.authority, input_data=raw)
    with authority_scope(runtime.authority):
        task = asyncio.create_task(runtime.engine.invoke(plan, handle, copy.deepcopy(raw)))
    try:
        async with asyncio.timeout(10):
            while services.invocations.get(handle.invocation_id).status != "waiting":
                assert not task.done(), "approval gate ended before authoritative waiting"
                await asyncio.sleep(0.01)
        assert (
            all(model.calls == [] for model in runtime.models.values()) and runtime.business == []
        )
        record = services.approvals.store.get(raw["requestId"])
        assert record is not None and record.status == "pending" and record.assignment_revision == 1
        original = services.approvals.protected_store.inspect(raw["requestId"])[0]
        actor = services.approvals.authorize_actor("Bearer controlled-role-caller-token")
        await services.approvals.decide(
            raw["requestId"],
            decision=decision,
            operator_id=actor.identity,
            actor=actor,
            value=None,
            revision=1,
            operation_key=uuid4().hex,
        )
        async with asyncio.timeout(10):
            result = await task
        assert (
            result.status == "completed"
            and services.approvals.store.get(raw["requestId"]).status == decision
        )
        assert services.approvals.protected_store.inspect(raw["requestId"])[0] == original
        assert len(runtime.models["approver-action"].calls) == (1 if decision == "approved" else 0)
        assert runtime.models["writer"].calls == [] and runtime.business == []
        assert services.invocations.read_input(handle) == raw
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_entire_role_batch_rejects_foreign_credential_payload_before_any_business(
    role_runtime,
):
    runtime = await role_runtime(
        responses=[
            {
                "tool_calls": [
                    {"name": "safe", "arguments": {}},
                    {"name": "safe", "arguments": {"security_profile": "foreign-owner"}},
                ]
            }
        ]
    )
    result, _, _ = await runtime.invoke({"input": "whole-batch-preflight"})
    assert result.status == "faulted" and len(runtime.models["writer"].calls) == 1
    assert runtime.business == []


@pytest.mark.asyncio
async def test_original_caller_denied_model_credential_ref_has_zero_secret_read_or_provider_io(
    role_runtime, monkeypatch
):
    from open_workflow_agent import catalog

    resolved = []
    called = []

    def secret(reference):
        resolved.append(reference.from_env)
        pytest.fail("denied provider credential was resolved")

    async def completion(**options):
        called.append(options)
        pytest.fail("denied provider credential reached external model SPI")

    monkeypatch.setattr(catalog, "resolve_secret", secret)
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=completion))
    runtime = await role_runtime(
        provider=True,
        credentials={"api_key": {"from_env": "OWA_OTHER_PRINCIPAL_MODEL_KEY"}},
        caller_rules=[
            {
                "actions": ["catalog.call", "model.complete"],
                "resources": ["*"],
                "roles": ["operator"],
            }
        ],
    )
    result, _, _ = await runtime.invoke({"input": "caller-is-allowed-model-not-this-credential"})
    assert result.status == "faulted" and called == [] and resolved == [] and runtime.business == []
    assert current_authority() is None


@pytest.mark.asyncio
async def test_entire_role_batch_preflights_exact_destination_permissions_before_first_io(
    role_runtime,
):
    rules = [
        {
            "actions": ["catalog.call", "model.complete", "tool.invoke"],
            "resources": ["*"],
            "roles": ["operator"],
        },
        {
            "actions": ["protocol.call"],
            "resources": ["openapi:https://tool.test/safe"],
            "roles": ["operator"],
        },
    ]
    runtime = await role_runtime(
        role_changes={"tools": ["safe", "admin"]},
        caller_rules=rules,
        responses=[
            {"tool_calls": [{"name": "safe", "arguments": {}}, {"name": "admin", "arguments": {}}]}
        ],
    )
    result, _, _ = await runtime.invoke({"input": "tool-name-grant-does-not-grant-destination"})
    assert result.status == "faulted" and len(runtime.models["writer"].calls) == 1
    assert runtime.business == [] and runtime.models["writer"].principals == [runtime.authority]
