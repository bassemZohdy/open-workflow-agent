from __future__ import annotations

import asyncio
import hashlib
import json
import os

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import ExecutionState, compile_workflow, generate_default_workflow
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.native import LANGGRAPH_AVAILABLE

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not LANGGRAPH_AVAILABLE, reason="native LangGraph dependency is absent"),
]


def _plan(tasks):
    workflow = generate_default_workflow()
    workflow["do"] = tasks
    return compile_workflow(workflow)


def _handle(services, plan, session_id="shared-session"):
    return services.invocations.create(
        engine="langgraph",
        session_id=session_id,
        user_id="native-user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )


async def test_native_scope_has_individual_nodes_conditional_edges_and_private_json_channels(
    services,
):
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor, _node_name

    plan = _plan(
        [
            {"first": {"set": {"count": 1}}},
            {"route": {"switch": [{"skip": {"when": "${ true }", "then": "last"}}]}},
            {"never": {"call": "llm:1.0.0@default"}},
            {"last": {"set": {"count": "${ .count + 1 }"}}},
        ]
    )
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    engine.native.database_path = None
    handle = _handle(services, plan)
    result = await engine.invoke(plan, handle, {})
    assert result.status == "completed", result.error
    assert result.output == {"count": 2}
    assert services.model.calls == []
    driver = LangGraphScopeExecutor(plan, engine.native.checkpointer, handle.invocation_id)
    graph = driver.compile_scope(plan.scopes[0], engine.executor, ExecutionState(data={}))
    nodes = graph.get_graph().nodes
    assert set(nodes) == {"__start__", "__end__", *(_node_name(task) for task in plan.tasks)}
    assert any(edge.conditional for edge in graph.get_graph().edges)
    checkpoints = list(engine.native.checkpointer.list(driver.checkpoint_config("/do", 1)))
    assert checkpoints
    snapshots = [checkpoint.checkpoint["channel_values"] for checkpoint in checkpoints]
    completed = [json.loads(value["snapshot"]) for value in snapshots if "snapshot" in value]
    assert any(value["outputs"] == {"first": {"count": 1}} for value in completed)
    assert any(
        "route" in value["outputs"] and "last" not in value["outputs"] for value in completed
    )
    assert all("never" not in value["outputs"] for value in completed)
    assert all(
        "metadata" not in value and "scope_executor" not in value and "expressions" not in value
        for value in completed
    )
    assert all(
        checkpoint.config["configurable"]["thread_id"] != handle.session_id
        for checkpoint in checkpoints
    )
    assert driver.checkpoint_config("/do", 1) != LangGraphScopeExecutor(
        plan, engine.native.checkpointer, "other-invocation"
    ).checkpoint_config("/do", 1)
    assert not hasattr(result, "snapshot")


@pytest.mark.parametrize("scope", ["root", "do", "for", "function", "import", "schema"])
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_restart_resumes_pending_native_task_without_replaying_completed_call(
    tmp_path, scope, backend
):
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    pytest.importorskip(
        "langgraph.checkpoint.postgres.aio" if datasource else "langgraph.checkpoint.sqlite.aio"
    )
    database_root = None if datasource else tmp_path
    calls = []
    catalog_fetches = []
    schema_fetches = []
    schema_bytes = b'{"type":"object"}'

    async def handler(request):
        if request.url.host == "schemas.test":
            schema_fetches.append(request)
            return httpx.Response(200, content=schema_bytes)
        if request.url.host == "catalog.test":
            catalog_fetches.append(request)
            return httpx.Response(
                200,
                json={
                    "call": "http",
                    "with": {"method": "POST", "endpoint": "https://service.test/once"},
                    "output": {"as": "${ . + {imported: true} }"},
                    "export": {"as": "${ {saved: $output.once} }"},
                },
            )
        calls.append(request.headers["Idempotency-Key"])
        return httpx.Response(200, json={"once": True})

    body = [
        {
            "sideEffect": {
                "call": "http",
                "with": {"method": "POST", "endpoint": "https://service.test/once"},
            }
        },
        {"pause": {"wait": {"seconds": 300}}},
        {"finish": {"set": {"done": True}}},
    ]
    tasks = (
        body
        if scope in {"root", "schema"}
        else [{"parent": {"do": body, **({"for": {"in": "${ [1] }"}} if scope == "for" else {})}}]
    )
    plan = _plan(tasks)
    if scope == "function":
        workflow = generate_default_workflow()
        workflow["use"] = {"functions": {"reusable": {"do": body}}}
        workflow["do"] = [{"parent": {"call": "reusable"}}]
        plan = compile_workflow(workflow)
    services = RuntimeServices(config, model=FakeModel(), database_root=database_root)
    if scope == "schema":
        from open_workflow_agent.config import ExternalCatalogConfig
        from open_workflow_agent.external_catalog import ExternalCatalogResolver
        from open_workflow_agent.workflow import resolve_and_compile_workflow

        uri = "https://schemas.test/object.json"
        workflow = generate_default_workflow()
        workflow["input"] = {"schema": {"resource": {"endpoint": uri}}}
        workflow["do"] = body
        resolver = ExternalCatalogResolver(
            {},
            resource_policies={
                "schema": ExternalCatalogConfig(
                    allowed_hosts=["schemas.test"],
                    allowed_endpoints=[uri],
                    integrity_pins={uri: hashlib.sha256(schema_bytes).hexdigest()},
                )
            },
            http=HttpClient(transport=httpx.MockTransport(handler)),
        )
        services.external_catalogs = resolver
        plan = await resolve_and_compile_workflow(
            workflow, resolver=resolver, catalog=services.catalog
        )
    if scope == "import":
        from open_workflow_agent.config import ExternalCatalogConfig
        from open_workflow_agent.external_catalog import ExternalCatalogResolver
        from open_workflow_agent.workflow import resolve_and_compile_workflow

        workflow = generate_default_workflow()
        workflow["use"] = {"catalogs": {"trusted": {"endpoint": "https://catalog.test/root"}}}
        workflow["do"] = [
            {"sideEffect": {"call": "echo:1.0.0@trusted"}},
            *body[1:],
        ]
        resolver = ExternalCatalogResolver(
            {"trusted": ExternalCatalogConfig(allowed_hosts=["catalog.test", "service.test"])},
            http=HttpClient(transport=httpx.MockTransport(handler)),
        )
        services.external_catalogs = resolver
        plan = await resolve_and_compile_workflow(
            workflow,
            trusted_catalogs=resolver.policies,
            resolver=resolver,
            catalog=services.catalog,
        )
    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    services.tools.protocols = services.protocols
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    handle = _handle(services, plan)
    running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
    try:
        async with asyncio.timeout(10):
            while not any(event.event_type == "TaskWaiting" for event in services.events.events):
                if running.done():
                    pytest.fail(str(running.result()))
                await asyncio.sleep(0.01)
        assert calls == [
            f"{handle.invocation_id}:"
            + (
                "/do/0/sideEffect"
                if scope in {"root", "schema"}
                else "/do/0/sideEffect/call/echo:1.0.0@trusted"
                if scope == "import"
                else "/do/0/parent/call/reusable/do/0/sideEffect"
                if scope == "function"
                else "/do/0/parent/do/0/sideEffect"
            )
        ]
        waiting = await asyncio.wait_for(asyncio.shield(running), timeout=10)
        assert waiting.status == "waiting", waiting.error
        assert handle.invocation_id not in engine._active
        assert handle.invocation_id not in engine._ownership_tasks
        assert handle._execution_lease is None
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = RuntimeServices(config, model=FakeModel(), database_root=database_root)
    if scope == "import":
        restarted.external_catalogs = resolver
    if scope == "schema":
        restarted.external_catalogs = ExternalCatalogResolver(
            {},
            resource_policies=resolver.schemas.policies,
            http=HttpClient(transport=httpx.MockTransport(handler)),
        )
    restarted.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    restarted.tools.protocols = restarted.protocols
    resumed_engine = LangGraphWorkflowEngine()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None
    assert persisted.status == "waiting"
    try:
        result = await asyncio.wait_for(
            resumed_engine.resume(persisted, {"resume": True}, plan), timeout=10
        )
        assert result.status == "completed", result.error
        assert result.output == {"done": True}
        assert len(calls) == 1
        if scope == "import":
            assert len(catalog_fetches) == 1
        if scope == "schema":
            assert len(schema_fetches) == 1
        assert not any(
            event.task_reference and event.task_reference.endswith("/sideEffect")
            for event in restarted.events.events
        )
    finally:
        await resumed_engine.shutdown()
        restarted.close()


async def test_concurrent_invocations_in_one_session_have_independent_native_checkpoints(services):
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    plan = _plan([{"echo": {"set": {"id": "${ .id }"}}}])
    handles = [_handle(services, plan) for _ in range(8)]
    results = await asyncio.gather(
        *(engine.invoke(plan, handle, {"id": index}) for index, handle in enumerate(handles))
    )
    assert [result.output for result in results] == [{"id": index} for index in range(8)]
    assert all(result.status == "completed" for result in results)


async def test_native_checkpoint_failure_is_sanitized_and_preserves_scope_reference(
    services, monkeypatch
):
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    engine.native.database_path = None
    plan = _plan([{"echo": {"set": {"ok": True}}}])

    async def fail(*args, **kwargs):
        raise RuntimeError("private-checkpoint-secret task_internal_native_id")

    monkeypatch.setattr(engine.native.checkpointer, "aput", fail)
    result = await engine.invoke(plan, _handle(services, plan), {})
    assert result.status == "faulted"
    assert result.error == {
        "code": "workflow_execution_error",
        "message": "native task execution failed",
        "details": {"reference": "/do"},
    }
    assert "private-checkpoint-secret" not in json.dumps(result.as_dict())
    assert "private-checkpoint-secret" not in json.dumps(
        [event.as_dict() for event in services.events.events]
    )
    assert engine.capabilities().as_dict()["features"]["nativeTaskExecution"] == {
        "nodes": True,
        "scopeStepLimit": 1000000,
    }


async def test_completed_fork_branch_does_not_advance_pending_sibling_cursor_on_resume(services):
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

    calls = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"done": True})

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    services.tools.protocols = services.protocols
    plan = _plan(
        [
            {
                "fanout": {
                    "fork": {
                        "branches": [
                            {
                                "completed": {
                                    "call": "http",
                                    "with": {
                                        "method": "POST",
                                        "endpoint": "https://service.test/complete",
                                    },
                                }
                            },
                            {
                                "pending": {
                                    "do": [
                                        {
                                            "beforeWait": {
                                                "call": "http",
                                                "with": {
                                                    "method": "POST",
                                                    "endpoint": "https://service.test/pending",
                                                },
                                            }
                                        },
                                        {"pause": {"wait": {"seconds": 300}}},
                                    ]
                                }
                            },
                        ]
                    }
                }
            }
        ]
    )
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    engine.native.database_path = None
    handle = _handle(services, plan)
    driver = LangGraphScopeExecutor(plan, engine.native.checkpointer, handle.invocation_id)
    scope = driver.scopes["/do/0/fanout/fork/branches/0"]
    completed_graph = driver.compile_scope(scope, engine.executor, ExecutionState(data={}))
    running = asyncio.create_task(engine.invoke(plan, handle, {}))
    try:
        async with asyncio.timeout(10):
            while True:
                saved = await completed_graph.aget_state(
                    driver.checkpoint_config(scope.reference, 1)
                )
                if saved.values and not saved.next and handle.status == "waiting":
                    break
                if running.done():
                    pytest.fail(str(running.result()))
                await asyncio.sleep(0.01)
        waiting = await asyncio.wait_for(asyncio.shield(running), timeout=10)
        assert waiting.status == "waiting", waiting.error
        assert handle.invocation_id not in engine._active
        assert handle.invocation_id not in engine._ownership_tasks
        assert handle._execution_lease is None
        result = await asyncio.wait_for(engine.resume(handle, {}, plan), timeout=10)
        assert result.status == "completed", result.error
        assert sorted(calls) == ["https://service.test/complete", "https://service.test/pending"]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_restart_preserves_ordered_fork_results_and_completed_branch_effects(
    tmp_path, backend
):
    from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("OWA_TEST_POSTGRES_URL is not configured")
    module = pytest.importorskip(
        "langgraph.checkpoint.postgres.aio" if datasource else "langgraph.checkpoint.sqlite.aio"
    )
    saver_type = module.AsyncPostgresSaver if datasource else module.AsyncSqliteSaver
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    database_root = None if datasource else tmp_path
    calls = []

    async def handler(request):
        calls.append((request.url.path, request.headers["Idempotency-Key"]))
        return httpx.Response(200, json={"shared": request.url.path.lstrip("/")})

    def action(branch):
        return {
            "call": "http",
            "with": {"method": "POST", "endpoint": f"https://service.test/{branch}"},
        }

    plan = _plan(
        [
            {
                "fanout": {
                    "fork": {
                        "branches": [
                            {"completed": action("completed")},
                            {
                                "pending": {
                                    "do": [
                                        {"action": action("pending")},
                                        {"pause": {"wait": {"seconds": 300}}},
                                    ]
                                }
                            },
                        ]
                    }
                }
            }
        ]
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=database_root)
    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    services.tools.protocols = services.protocols
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    handle = _handle(services, plan)
    driver = LangGraphScopeExecutor(plan, None, handle.invocation_id)
    completed_scope = driver.scopes["/do/0/fanout/fork/branches/0"]
    running = asyncio.create_task(engine.invoke(plan, handle, {"original": True}))
    try:
        async with asyncio.timeout(10):
            async with saver_type.from_conn_string(engine.native.database_path) as probe:
                await probe.setup()
                driver = LangGraphScopeExecutor(plan, probe, handle.invocation_id)
                graph = driver.compile_scope(
                    completed_scope, engine.executor, ExecutionState(data={})
                )
                while True:
                    saved = await graph.aget_state(
                        driver.checkpoint_config(completed_scope.reference, 1)
                    )
                    if saved.values and not saved.next and handle.status == "waiting":
                        break
                    if running.done():
                        pytest.fail(str(running.result()))
                    await asyncio.sleep(0.01)
                assert json.loads(saved.values["snapshot"])["data"] == {"shared": "completed"}
        waiting = await asyncio.wait_for(asyncio.shield(running), timeout=10)
        assert waiting.status == "waiting", waiting.error
        assert handle.invocation_id not in engine._active
        assert handle.invocation_id not in engine._ownership_tasks
        assert handle._execution_lease is None
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await engine.shutdown()
        services.close()

    restarted = RuntimeServices(config, model=FakeModel(), database_root=database_root)
    restarted.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(handler)))
    restarted.tools.protocols = restarted.protocols
    resumed_engine = LangGraphWorkflowEngine()
    await resumed_engine.initialize(restarted)
    persisted = restarted.invocations.get(handle.invocation_id)
    assert persisted is not None and persisted.status == "waiting"
    expected = [{"shared": "completed"}, {"shared": "pending"}]
    try:
        result = await asyncio.wait_for(resumed_engine.resume(persisted, {}, plan), timeout=10)
        assert result.status == "completed", result.error
        assert result.output == expected
        assert sorted(path for path, _ in calls) == ["/completed", "/pending"]
        assert len({operation_id for _, operation_id in calls}) == 2
        async with saver_type.from_conn_string(resumed_engine.native.database_path) as probe:
            await probe.setup()
            driver = LangGraphScopeExecutor(plan, probe, handle.invocation_id)
            root = driver.scopes["/do"]
            graph = driver.compile_scope(root, resumed_engine.executor, ExecutionState(data={}))
            saved = await graph.aget_state(driver.checkpoint_config("/do", 1))
            assert json.loads(saved.values["snapshot"])["data"] == expected
    finally:
        await resumed_engine.shutdown()
        restarted.close()
