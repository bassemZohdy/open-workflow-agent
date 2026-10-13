from __future__ import annotations

import asyncio

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import ToolError
from open_workflow_agent.events import InMemoryEventBus
from open_workflow_agent.external_catalog import ExternalCatalogResolver
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.services import RuntimeServices


@pytest.mark.asyncio
async def test_invoke_capabilities_health_and_reload(tmp_path):
    services = RuntimeServices(
        RuntimeConfig(), model=FakeModel({"response": "hello"}), database_root=tmp_path
    )
    app = create_app(config=RuntimeConfig(), services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health/live")).json() == {"status": "ok"}
            capabilities = (await client.get("/v1/capabilities")).json()
            assert capabilities["workflowDsl"] == "1.0.3"
            assert capabilities["protocols"] == ["http", "mcp", "a2a", "openapi"]
            assert capabilities["policies"] == ["retry", "timeout"]
            assert {"try", "wait", "raise"} <= set(capabilities["tasks"])
            assert capabilities["features"]["cancellation"] is True
            assert capabilities["features"]["waiting"] is True
            assert capabilities["features"]["events"] == {
                "emit": True,
                "listen": True,
                "durable": True,
            }
            assert capabilities["features"]["cloudEvents"] == {
                "lifecycle": True,
                "specversion": "1.0",
                "delivery": "bounded_snapshot",
                "durable": False,
            }
            assert capabilities["features"]["subWorkflows"] == {
                "run": True,
                "separateInvocation": True,
                "localCatalog": True,
                "externalCatalog": False,
            }
            assert capabilities["features"]["catalogs"]["enabled"] is False
            event = await client.post(
                "/v1/events",
                json={"event": {"id": "api-event-1", "type": "api.test", "data": {"ok": True}}},
            )
            assert event.status_code == 200
            assert event.json()["id"] == "api-event-1"
            result = await client.post("/v1/invoke", json={"input": {"question": "hi"}})
            assert result.status_code == 200
            assert result.json()["status"] == "completed"
            lifecycle = await client.get("/v1/events/lifecycle?limit=2")
            assert lifecycle.status_code == 200
            assert lifecycle.headers["content-type"].startswith(
                "application/cloudevents-batch+json"
            )
            assert len(lifecycle.json()) == 2
            assert all(event["specversion"] == "1.0" for event in lifecycle.json())
            assert (await client.post("/v1/admin/knowledge/reload")).status_code == 200


@pytest.mark.asyncio
async def test_api_limits_payloads_and_normalizes_not_found(tmp_path):
    config = RuntimeConfig.model_validate(
        {"model": {"provider": "fake"}, "server": {"max_request_bytes": 32}}
    )
    services = RuntimeServices(
        config, model=FakeModel({"response": "hello"}), database_root=tmp_path
    )
    app = create_app(config=config, services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health/ready")).json() == {
                "status": "ok",
                "checks": {"database": "ok", "knowledge": "ok", "model": "ok"},
            }
            oversized = await client.post("/v1/invoke", json={"input": "x" * 100})
            assert oversized.status_code == 413
            assert oversized.json()["error"]["code"] == "request_too_large"
            missing = await client.post("/v1/invocations/missing/resume", json={"input": {}})
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "invocation_not_found"
            malformed = await client.post("/v1/invoke", json={"unexpected": True})
            assert malformed.status_code == 422
            assert malformed.json()["error"]["code"] == "request_validation_error"


@pytest.mark.asyncio
async def test_api_does_not_report_ready_when_external_catalog_is_unavailable(tmp_path):
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "api-catalog",
            "name": "unavailable",
            "version": "1.0.0",
        },
        "use": {"catalogs": {"trusted": {"endpoint": {"uri": "https://catalog.test/root"}}}},
        "do": [{"remote": {"call": "echo:1.0.0@trusted"}}],
    }
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "workflow": {
                "definition": workflow,
                "external_catalogs": {"trusted": {"allowed_hosts": ["catalog.test"]}},
            },
        }
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="temporarily unavailable")

    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    services.external_catalogs = ExternalCatalogResolver(
        config.workflow.external_catalogs,
        http=HttpClient(transport=httpx.MockTransport(handler)),
    )
    app = create_app(config=config, services=services)
    try:
        with pytest.raises(ToolError) as failure:
            async with app.router.lifespan_context(app):
                raise AssertionError("unavailable catalog must prevent startup")
        assert app.state.ready is False
        assert not hasattr(app.state, "plan")
        assert "temporarily unavailable" not in str(failure.value)
        assert "catalog.test" not in repr(failure.value.details)
        assert services.external_catalogs.capabilities()["states"]["trusted"] == "unavailable"
    finally:
        services.close()


@pytest.mark.asyncio
async def test_api_cancel_waiting_invocation_is_idempotent(tmp_path):
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "workflow": {
                "definition": {
                    "document": {
                        "dsl": "1.0.3",
                        "namespace": "tests",
                        "name": "api-cancel",
                        "version": "1.0.0",
                    },
                    "do": [{"pause": {"wait": {"seconds": 5}}}],
                }
            },
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    app = create_app(config=config, services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            invoke_task = asyncio.create_task(client.post("/v1/invoke", json={"input": {}}))
            for _ in range(100):
                waiting = next(
                    (
                        event
                        for event in services.events.events
                        if event.event_type == "WorkflowWaiting"
                    ),
                    None,
                )
                if waiting is not None:
                    break
                await asyncio.sleep(0.005)
            assert waiting is not None
            cancelled = await client.post(
                f"/v1/invocations/{waiting.invocation_id}/cancel",
                headers={"Idempotency-Key": "cancel-api-1"},
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
            duplicate = await client.post(
                f"/v1/invocations/{waiting.invocation_id}/cancel",
                headers={"Idempotency-Key": "cancel-api-1"},
            )
            assert duplicate.json()["status"] == "cancelled"
            invoke_result = await invoke_task
            assert invoke_result.status_code == 200
            assert invoke_result.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_api_schedule_is_durable_and_idempotent(tmp_path):
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "workflow": {
                "definition": {
                    "document": {
                        "dsl": "1.0.3",
                        "namespace": "tests",
                        "name": "api-schedule",
                        "version": "1.0.0",
                    },
                    "schedule": {"after": {"milliseconds": 1}},
                    "do": [{"finish": {"set": {"done": True}}}],
                }
            },
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    app = create_app(config=config, services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/v1/schedules",
                json={"input": {"from": "api"}},
                headers={"Idempotency-Key": "schedule-api-1"},
            )
            duplicate = await client.post(
                "/v1/schedules",
                json={"input": {"from": "ignored"}},
                headers={"Idempotency-Key": "schedule-api-1"},
            )
            assert first.status_code == duplicate.status_code == 200
            assert first.json()["schedule_id"] == duplicate.json()["schedule_id"]
            schedule_id = first.json()["schedule_id"]
            for _ in range(1000):
                current = await client.get(f"/v1/schedules/{schedule_id}")
                assert current.status_code == 200, current.text
                if current.json().get("last_status") == "completed":
                    break
                await asyncio.sleep(0.005)
            assert current.json()["status"] == "active"
            assert current.json().get("last_status") == "completed", current.text
            handle = services.invocations.get(current.json()["last_invocation_id"])
            assert handle is not None and handle.status == "completed"
            assert handle.output == {"done": True}
            assert services.invocations.read_input(handle) == {"from": "api"}
            assert services.model.calls == []
            cancelled = await client.post(
                f"/v1/schedules/{schedule_id}/cancel",
                headers={"Idempotency-Key": "schedule-cancel-1"},
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
    services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["embedded", "memory"])
@pytest.mark.parametrize("authenticated", [False, True])
async def test_api_event_schedule_dispatches_projected_input_and_stops_after_cancel(
    tmp_path, backend, authenticated, monkeypatch
):
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "execution_timing": {"timezone": "Asia/Dubai"},
            "events": {"backend": backend},
            "workflow": {
                "definition": {
                    "document": {
                        "dsl": "1.0.3",
                        "namespace": "tests",
                        "name": "api-event-schedule",
                        "version": "1.0.0",
                    },
                    "schedule": {"on": {"one": {"with": {"type": "ready"}}}},
                    "do": [{"finish": {"set": {"triggered": "${ $workflow.input }"}}}],
                }
            },
        }
    )
    headers = {}
    principal = "anonymous"
    if authenticated:
        monkeypatch.setenv("SCHEDULE_TEST_TOKEN", "schedule-test-token")
        settings = config.model_dump(exclude_unset=True)
        settings["server"] = {"api_security_profile": "schedule-auth"}
        settings["security"] = {
            "profiles": {
                "schedule-auth": {
                    "type": "bearer",
                    "token": {"from_env": "SCHEDULE_TEST_TOKEN"},
                    "principal": "schedule-test-client",
                }
            }
        }
        config = RuntimeConfig.model_validate(settings)
        headers = {"Authorization": "Bearer schedule-test-token"}
        principal = "schedule-test-client"
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    app = create_app(config=config, services=services)
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", headers=headers
            ) as client:
                if authenticated:
                    denied = await client.post(
                        "/v1/schedules", json={"input": {}}, headers={"Authorization": ""}
                    )
                    assert denied.status_code == 403
                    assert denied.json()["error"]["code"] == "authentication_failed"
                    assert services.schedules.active_triggers() == ()
                created = await client.post("/v1/schedules", json={"input": {"registration": True}})
                if backend == "memory":
                    assert created.status_code == 400, created.text
                    assert created.json()["error"]["code"] == "schedule_validation_error"
                    assert services.schedules.active_triggers() == ()
                    return
                assert created.status_code == 200, created.text
                schedule_id = created.json()["schedule_id"]
                record = services.schedules.get(schedule_id)
                assert record is not None and record.timezone == "Asia/Dubai"
                assert record.owner_principal == record.event_scope == principal
                # Register before publication so pre-registration events cannot start a cycle.
                await app.state.scheduler.tick()
                published = await client.post(
                    "/v1/events", json={"event": {"type": "ready", "data": {"value": None}}}
                )
                assert published.status_code == 200, published.text
                for _ in range(1000):
                    current = await client.get(f"/v1/schedules/{schedule_id}")
                    assert current.status_code == 200, current.text
                    if current.json().get("last_status") == "completed":
                        break
                    await asyncio.sleep(0.005)
                assert current.json().get("last_status") == "completed", current.text
                handle = services.invocations.get(current.json()["last_invocation_id"])
                assert handle is not None and handle.output == {"triggered": [{"value": None}]}
                cancelled = await client.post(f"/v1/schedules/{schedule_id}/cancel")
                assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
                assert (
                    await client.post(
                        "/v1/events", json={"event": {"type": "ready", "data": {"value": 2}}}
                    )
                ).status_code == 200
                await app.state.scheduler.tick()
                assert (
                    services.schedules.get(schedule_id).last_invocation_id == handle.invocation_id
                )
    finally:
        services.close()


@pytest.mark.asyncio
async def test_api_runs_a_registered_local_subworkflow(tmp_path):
    child = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "api-subworkflow",
            "name": "child",
            "version": "1.0.0",
        },
        "do": [{"make_child": {"set": {"child": "${ .value }"}}}],
    }
    parent = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "api-subworkflow",
            "name": "parent",
            "version": "1.0.0",
        },
        "do": [
            {
                "child": {
                    "run": {
                        "workflow": {
                            "namespace": "api-subworkflow",
                            "name": "child",
                            "version": "1.0.0",
                            "input": {"value": "${ .value }"},
                        }
                    }
                }
            },
            {"finish": {"set": {"result": "${ .child }"}}},
        ],
    }
    config = RuntimeConfig.model_validate(
        {
            "model": {"provider": "fake"},
            "workflow": {"definition": parent, "catalog": [child]},
        }
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path)
    app = create_app(config=config, services=services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/invoke", json={"input": {"value": "api"}})
            assert response.status_code == 200
            assert response.json()["output"] == {"result": "api"}
    services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["embedded", "embedded-approval", "memory", "custom"])
@pytest.mark.parametrize(
    "engine_name,engine_type", [("base", PortableWorkflowEngine), *engine_cases()]
)
async def test_scheduling_capabilities_match_known_event_backend(
    tmp_path, monkeypatch, backend, engine_name, engine_type
):
    settings = {
        "model": {"provider": "fake"},
        "events": {"backend": "memory" if backend == "memory" else "embedded"},
        "workflow": {
            "definition": {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "tests",
                    "name": "capability-event-schedule",
                    "version": "1.0.0",
                },
                "schedule": {"on": {"one": {"with": {"type": "ready"}}}},
                "do": [{"finish": {"set": {"done": True}}}],
            }
        },
    }
    if backend == "embedded-approval":
        monkeypatch.setenv("SCHEDULE_CAPABILITY_OPERATOR_TOKEN", "test-operator-token")
        settings["approvals"] = {"enabled": True, "operator_security_profile": "operator"}
        settings["security"] = {
            "profiles": {
                "operator": {
                    "type": "bearer",
                    "token": {"from_env": "SCHEDULE_CAPABILITY_OPERATOR_TOKEN"},
                }
            }
        }
    config = RuntimeConfig.model_validate(settings)
    services = RuntimeServices(
        config,
        model=FakeModel(),
        database_root=tmp_path,
        event_bus=InMemoryEventBus() if backend == "custom" else None,
    )
    app = create_app(config=config, services=services, engine=engine_type())
    expected = backend in {"embedded", "embedded-approval"}
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                capabilities = (await client.get("/v1/capabilities")).json()
                assert capabilities["engine"] == engine_name
                assert capabilities["features"]["scheduling"] == {
                    "after": True,
                    "every": True,
                    "cron": True,
                    "on": expected,
                    "durable": True,
                    "owner": "fenced_workers",
                }
                created = await client.post("/v1/schedules", json={"input": {}})
                assert created.status_code == (200 if expected else 400), created.text
                if expected:
                    cancelled = await client.post(
                        f"/v1/schedules/{created.json()['schedule_id']}/cancel"
                    )
                    assert cancelled.status_code == 200
                assert services.model.calls == []
    finally:
        services.close()
