"""Actual native bounded OWS patterns with distinct deployment-owned roles."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from _agent_pattern_fixtures import (
    ENGINES,
    PATTERNS,
    actual_native,
    assert_roles,
    compiled,
    configuration,
    create,
    deployed_openapi_tool,
    expected,
    invoke,
    original_authority,
    providers,
    receipts,
    register_workers,
    retained,
    role_configuration,
    runtime,
    scripts,
)
from open_workflow_agent.approvals import APPROVAL_DECISION_EVENT, APPROVAL_REQUEST_EVENT
from open_workflow_agent.errors import OwaError
from open_workflow_agent.execution_authority import authority_scope

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")
QUESTION = {"question": "hello"}
ROLE_REFERENCES = {
    "assistant": ["/do/0/respond"],
    "pipeline": ["/do/0/draft", "/do/1/refine"],
    "multi_perspective": [
        "/do/0/perspectives/fork/branches/0/research",
        "/do/0/perspectives/fork/branches/1/risk",
        "/do/1/synthesize",
    ],
    "refine_until_good": ["/do/0/attempts/do/0/draft", "/do/0/attempts/do/1/evaluate"],
    "expert_dispatch": ["/do/0/classify", "/do/3/research"],
    "team_coordinator": ["/do/1/synthesize"],
    "plan_and_execute": ["/do/0/plan", "/do/1/execute/do/2/research", "/do/1/execute/do/3/finance"],
    "approval_gate": ["/do/3/act"],
}


def approval_input(seconds=120):
    return {
        "question": "hello",
        "requestId": uuid4().hex,
        "expiresAt": datetime.fromtimestamp(time.time() + seconds, UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "assignee": "reviewer",
        "reassignmentTargets": ["alternate"],
        "escalationTargets": ["lead"],
    }


def actor(services, name):
    selected = services.approvals.authorize_actor("Bearer fixture-actor-" + name)
    assert selected.identity == name
    return selected


async def waiting(services, handle, task):
    async with asyncio.timeout(40):
        while services.invocations.get(handle.invocation_id).status != "waiting":
            if task.done():
                raise AssertionError(await task)
            await asyncio.sleep(0.01)


async def decision(services, original, name="reviewer", status="approved", revision=1):
    selected = actor(services, name)
    return await services.approvals.decide(
        original["requestId"],
        decision=status,
        operator_id=selected.identity,
        actor=selected,
        revision=revision,
        value={"approved": status == "approved"},
        operation_key=uuid4().hex,
    )


def verify_known_pattern(services, handle, backend, pattern, original):
    evidence = retained(services, handle, backend)
    assert evidence["input"] == original and evidence["owner"] == "alice"
    assert evidence["authority"] == original_authority(services).as_value()
    assert evidence["clock"]["profile"] == "owa/timing/v1"
    assert evidence["native"]["present"] and evidence["receipts"]
    role_receipts = [
        (identity, receipt)
        for identity, receipt in receipts(services, handle)
        if identity.operation.startswith("call:agent-")
    ]
    assert sorted({identity.task_reference for identity, _ in role_receipts}) == sorted(
        ROLE_REFERENCES[pattern]
    )
    assert all(receipt.status == "completed" for _, receipt in role_receipts)
    encoded = json.dumps(evidence, ensure_ascii=False)
    assert "fixture-actor-" not in encoded
    return evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("pattern", PATTERNS)
async def test_all_eight_declared_role_patterns_use_original_source_input_and_native_receipts(
    tmp_path, engine_name, engine_type, backend, pattern
):
    roles, models = providers(pattern)
    config = configuration(backend, roles, approvals=pattern == "approval_gate")
    services = runtime(tmp_path, backend, roles, models, config=config)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    if pattern == "team_coordinator":
        register_workers(services)
    plan = compiled(services, pattern)
    original = approval_input() if pattern == "approval_gate" else copy.deepcopy(QUESTION)
    handle = create(services, engine_name, plan)
    task = asyncio.create_task(invoke(engine, services, plan, handle, original))
    try:
        if pattern == "approval_gate":
            await waiting(services, handle, task)
            assert models["approver-action"].calls == []
            record = await decision(services, original)
            assert record.status == "approved"
        result = await asyncio.wait_for(task, 50)
        assert result.status == "completed", result.error
        assert result.output == expected(pattern) and services.model.calls == []
        if pattern != "approval_gate":
            assert_roles(models, pattern)
        else:
            assert len(models["approver-action"].calls) == 1
            payload = models["approver-action"].calls[0]["input"]
            assert payload["question"] == "hello" and payload["approval"]["decision"] == "approved"
            assert payload["approval"]["value"] == {"approved": True}
        before = verify_known_pattern(services, handle, backend, pattern, original)
        counts = {name: len(model.calls) for name, model in models.items()}
        assert await engine.resume(handle, {"question": "replacement"}, plan) == result
        assert retained(services, handle, backend) == before
        assert {name: len(model.calls) for name, model in models.items()} == counts
        if pattern == "team_coordinator":
            bindings = services.effect_journal.connection.execute(
                "SELECT child_id,parent_task_reference FROM child_workflow_bindings "
                "WHERE parent_id=? ORDER BY parent_task_reference",
                (handle.invocation_id,),
            ).fetchall()
            assert [row[1] for row in bindings] == [
                "/do/0/workers/fork/branches/0/researcher",
                "/do/0/workers/fork/branches/1/finance",
            ]
            for child_id, _ in bindings:
                child = services.invocations.get(child_id)
                assert child.status == "completed" and child.owner_principal == "alice"
                assert services.invocations.read_input(child) == original
                assert services.execution_authorizations.verify_invocation(
                    child
                ) == original_authority(services)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_perspectives_finish_in_reverse_order_but_synthesize_declaration_order(
    tmp_path, engine_name, engine_type, backend
):
    started, risk_finished = asyncio.Event(), asyncio.Event()
    finished = []

    async def research(prompt):
        started.set()
        await risk_finished.wait()
        finished.append("research")
        return {"answer": "research"}

    async def risk(prompt):
        await started.wait()
        finished.append("risk")
        risk_finished.set()
        return {"answer": "risk"}

    roles, models = providers("multi_perspective", responses={"researcher": research, "risk": risk})
    services = runtime(tmp_path, backend, roles, models)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    plan = compiled(services, "multi_perspective")
    handle = create(services, engine_name, plan)
    try:
        result = await asyncio.wait_for(invoke(engine, services, plan, handle, QUESTION), 50)
        assert result.status == "completed" and result.output == {"answer": "combined"}, (
            result.error
        )
        assert finished == ["risk", "research"]
        assert_roles(models, "multi_perspective")
        verify_known_pattern(services, handle, backend, "multi_perspective", QUESTION)
    finally:
        risk_finished.set()
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_refinement_exhausts_original_three_attempt_budget_without_fourth_provider_call(
    tmp_path, engine_name, engine_type, backend
):
    script = {
        "drafter": [{"text": "first"}, {"text": "second"}, {"text": "third"}],
        "critic": [{"quality": False, "feedback": "improve"}] * 3,
    }
    roles, models = providers("refine_until_good", script=script)
    services = runtime(tmp_path, backend, roles, models)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    plan = compiled(services, "refine_until_good")
    handle = create(services, engine_name, plan)
    try:
        result = await invoke(engine, services, plan, handle, QUESTION)
        assert result.status == "faulted" and result.error["details"]["status"] == 422
        assert result.error["details"]["instance"] == "/do/2/reject"
        assert len(models["drafter"].calls) == len(models["critic"].calls) == 3
        assert models["drafter"].calls[2]["input"] == {
            "question": "hello",
            "previous": "second",
            "feedback": "improve",
        }
        assert await engine.resume(handle, {}, plan) == result
        assert len(models["drafter"].calls) == len(models["critic"].calls) == 3
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_expert_finance_route_does_not_call_unselected_researcher(
    tmp_path, engine_name, engine_type, backend
):
    script = scripts("expert_dispatch")
    script["router"] = [{"route": "finance"}]
    roles, models = providers("expert_dispatch", script=script)
    services = runtime(tmp_path, backend, roles, models)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    plan = compiled(services, "expert_dispatch")
    handle = create(services, engine_name, plan)
    try:
        result = await invoke(engine, services, plan, handle, QUESTION)
        assert result.status == "completed" and result.output == {"answer": "finance"}, result.error
        assert models["researcher"].calls == []
        assert [prompt["input"] for prompt in models["finance"].calls] == ["hello"]
        selected = [
            identity.task_reference
            for identity, _ in receipts(services, handle)
            if identity.operation.startswith("call:agent-")
        ]
        assert sorted(selected) == ["/do/0/classify", "/do/4/finance"]
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "case", ["unknown-route", "recursive-plan", "oversize-plan", "response-schema"]
)
async def test_untrusted_role_output_cannot_choose_unknown_recursive_or_unschematized_actions(
    tmp_path, engine_name, engine_type, backend, case
):
    pattern = (
        "expert_dispatch"
        if case == "unknown-route"
        else "assistant"
        if case == "response-schema"
        else "plan_and_execute"
    )
    script = scripts(pattern)
    if case == "unknown-route":
        script["router"] = [{"route": "administrator"}]
    elif case == "recursive-plan":
        script["planner"] = [{"actions": [{"action": "plan-and-execute", "input": "recurse"}]}]
    elif case == "oversize-plan":
        script["planner"] = [{"actions": [{"action": "research", "input": "too many"}] * 4}]
    else:
        script["assistant"] = [{"answer": 123}]
    roles, models = providers(pattern, script=script)
    services = runtime(tmp_path, backend, roles, models)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    plan = compiled(services, pattern)
    handle = create(services, engine_name, plan)
    try:
        result = await invoke(engine, services, plan, handle, QUESTION)
        assert result.status == "faulted" and result.error["code"] == "workflow_semantic_error"
        assert result.error["details"]["status"] == 400
        assert sum(len(model.calls) for model in models.values()) == 1
        if "researcher" in models:
            assert models["researcher"].calls == [] and models["finance"].calls == []
        assert (
            services.effect_journal.connection.execute(
                "SELECT COUNT(*) FROM child_workflow_bindings WHERE parent_id=?",
                (handle.invocation_id,),
            ).fetchone()[0]
            == 0
        )
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("malicious", [False, True], ids=["allowed-tool", "mixed-forbidden-batch"])
async def test_native_role_uses_only_its_declared_tool_batch_before_any_business_effect(
    tmp_path, engine_name, engine_type, backend, malicious
):
    business = []

    async def dispatch(request):
        assert request.method == "GET" and str(request.url) == "https://patterns.test/lookup"
        assert request.content == b""
        business.append(str(request.url))
        return httpx.Response(200, json={"result": "read"})

    calls = [{"name": "lookup", "arguments": {}}]
    if malicious:
        calls.append({"name": "add_memory", "arguments": {"text": "unapproved"}})
    roles, models = providers(
        "assistant", script={"assistant": [{"tool_calls": calls}, {"answer": "assistant"}]}
    )
    roles["assistant"] = role_configuration("assistant", tools=["lookup"])
    config = configuration(
        backend,
        roles,
        tools=[deployed_openapi_tool("lookup", "GET")],
    )
    services = runtime(tmp_path, backend, roles, models, config=config, dispatch=dispatch)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    plan = compiled(services, "assistant")
    handle = create(services, engine_name, plan)
    try:
        result = await invoke(engine, services, plan, handle, QUESTION)
        assert models["assistant"].calls[0]["tools"] == ["lookup"]
        if malicious:
            assert result.status == "faulted" and result.error["code"] == "tool_error"
            assert business == [] and len(models["assistant"].calls) == 1
        else:
            assert result.status == "completed" and result.output == {"answer": "assistant"}
            assert (
                business == ["https://patterns.test/lookup"] and len(models["assistant"].calls) == 2
            )
            assert models["assistant"].calls[1]["tool_results"] == [
                {"name": "lookup", "result": {"result": "read"}}
            ]
        assert services.model.calls == []
    finally:
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_parent_control_cancels_both_actual_team_children_and_never_synthesizes(
    tmp_path, engine_name, engine_type, backend
):
    started = {role: asyncio.Event() for role in ("researcher", "finance")}

    async def response(role, prompt):
        started[role].set()
        await asyncio.Event().wait()

    roles, models = providers(
        "team_coordinator",
        responses={role: (lambda prompt, role=role: response(role, prompt)) for role in started},
    )
    services = runtime(tmp_path, backend, roles, models)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    register_workers(services)
    plan = compiled(services, "team_coordinator")
    handle = create(services, engine_name, plan)
    task = asyncio.create_task(invoke(engine, services, plan, handle, QUESTION))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 40)
        children = services.effect_journal.connection.execute(
            "SELECT child_id FROM child_workflow_bindings WHERE parent_id=?",
            (handle.invocation_id,),
        ).fetchall()
        assert len(children) == 2
        with authority_scope(original_authority(services)):
            cancelled = await engine.cancel(handle, operation_id="cancel-original-team")
        assert cancelled.status == "cancelled"
        assert (await asyncio.wait_for(task, 30)).status == "cancelled"
        for (child_id,) in children:
            child = services.invocations.get(child_id)
            assert child.status == "cancelled" and child.owner_principal == "alice"
            assert child.invocation_id not in engine._active and child._execution_lease is None
        assert models["synthesizer"].calls == []
        assert len(models["researcher"].calls) == len(models["finance"].calls) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.shutdown()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_name,engine_type", ENGINES)
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    "case", ["rejected", "expired", "reassign", "escalate", "unknown", "denied", "reopen"]
)
async def test_approval_pattern_stays_pending_until_original_protected_control_authorizes_role(
    tmp_path, engine_name, engine_type, backend, case
):
    business = []

    async def dispatch(request):
        assert request.method == "POST"
        assert str(request.url.copy_with(query=None)) == "https://patterns.test/commit"
        assert request.content == b"" and dict(request.url.params) == {"question": "hello"}
        business.append(dict(request.url.params))
        return httpx.Response(200, json={"committed": True})

    roles, models = providers(
        "approval_gate",
        script={
            "approver-action": [
                {
                    "tool_call": {
                        "name": "commit",
                        "arguments": {"parameters": {"question": "hello"}},
                    }
                },
                {"answer": "approved action"},
            ]
        },
    )
    roles["approver-action"] = role_configuration("approver-action", tools=["commit"])
    config = configuration(
        backend,
        roles,
        approvals=True,
        tools=[
            deployed_openapi_tool(
                "commit",
                "POST",
                parameters=[
                    {
                        "name": "question",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
            )
        ],
    )
    services = runtime(tmp_path, backend, roles, models, config=config, dispatch=dispatch)
    engine = engine_type()
    actual_native(engine, engine_name)
    await engine.initialize(services)
    if case == "expired":
        await services.stop_protected_approvals()
    if case in {"rejected", "expired"}:
        # A durable listener scans retained scoped events from its initial
        # cursor. An earlier approved event for another request must not
        # authorize this invocation's action role.
        stale_id = uuid4().hex
        await services.event_bus.publish_scoped(
            {
                "id": stale_id,
                "source": "urn:pattern:prior-approval",
                "type": APPROVAL_REQUEST_EVENT,
                "subject": stale_id,
                "data": {"question": "prior request"},
            },
            default_source="urn:pattern:prior-approval",
            scope="alice",
        )
        await services.approvals.decide(
            stale_id,
            decision="approved",
            operator_id="prior-reviewer",
            value={"approved": True},
            operation_key=uuid4().hex,
        )
    original = approval_input(3 if case == "expired" else 120)
    plan = compiled(services, "approval_gate")
    handle = create(services, engine_name, plan)
    task = asyncio.create_task(invoke(engine, services, plan, handle, original))
    try:
        await waiting(services, handle, task)
        request = services.approvals.store.get(original["requestId"])
        expires_at = request.expires_at
        assert (
            request.status == "pending" and models["approver-action"].calls == [] and business == []
        )
        reviewer, revision, terminal = "reviewer", 1, "approved"
        if case in {"reassign", "escalate"}:
            reviewer = "alternate" if case == "reassign" else "lead"
            changed = await services.approvals.mutate_protected(
                original["requestId"],
                action=case,
                actor=actor(services, "reviewer"),
                revision=1,
                operation_key=uuid4().hex,
                assignee=reviewer,
            )
            assert changed.assignment_revision == 2 and changed.expires_at == expires_at
            with pytest.raises(OwaError):
                await decision(services, original, revision=2)
            revision = 2
        elif case == "unknown":
            with pytest.raises(ValueError):
                await services.event_bus.publish_scoped(
                    {
                        "type": APPROVAL_DECISION_EVENT,
                        "subject": original["requestId"],
                        "data": {"decision": "unknown"},
                    },
                    default_source="urn:pattern:untrusted",
                    scope="alice",
                )
            assert services.approvals.store.get(original["requestId"]).status == "pending"
            assert models["approver-action"].calls == [] and business == []
        elif case == "denied":
            with pytest.raises(OwaError):
                await decision(services, original, name="alternate")
            assert services.approvals.store.get(original["requestId"]).status == "pending"
            assert models["approver-action"].calls == [] and business == []
        elif case == "reopen":
            await engine.shutdown()
            await asyncio.gather(task, return_exceptions=True)
            services.close()
            services = runtime(tmp_path, backend, roles, models, config=config, dispatch=dispatch)
            engine = engine_type()
            await engine.initialize(services)
            handle = services.invocations.get(handle.invocation_id)
            plan = services.resolve_original_definition(handle)
            assert services.approvals.store.get(original["requestId"]).expires_at == expires_at
            with authority_scope(original_authority(services)):
                task = asyncio.create_task(engine.resume(handle, {"substitute": True}, plan))
        if case == "expired":
            async with asyncio.timeout(15):
                while time.time() < expires_at:
                    await asyncio.sleep(0.01)
            assert services.approvals.store.get(original["requestId"]).status == "pending"
            await services.start_protected_approvals()
            terminal = "expired"
        else:
            terminal = "rejected" if case == "rejected" else "approved"
            await decision(services, original, reviewer, terminal, revision)
        result = await asyncio.wait_for(task, 50)
        record = services.approvals.store.get(original["requestId"])
        assert result.status == "completed", result.error
        assert record.status == terminal and record.expires_at == expires_at
        assert len(models["approver-action"].calls) == (2 if terminal == "approved" else 0)
        assert business == ([{"question": "hello"}] if terminal == "approved" else [])
        if terminal == "approved":
            assert result.output == expected("approval_gate")
        else:
            assert result.output[0]["decision"] == terminal
        assert services.invocations.read_input(handle) == original and services.model.calls == []
        assert await engine.resume(handle, {}, plan) == result
        assert len(models["approver-action"].calls) == (2 if terminal == "approved" else 0)
        assert business == ([{"question": "hello"}] if terminal == "approved" else [])
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.shutdown()
        services.close()
