from __future__ import annotations

from fastapi.testclient import TestClient
from open_workflow_agent.api import create_app
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.metrics import Metrics
from open_workflow_agent.observability import WorkflowEvent
from open_workflow_agent.services import RuntimeServices


def test_metrics_registry_renders_lifecycle_and_histogram_samples() -> None:
    metrics = Metrics()
    metrics.observe_lifecycle(
        WorkflowEvent(
            event_type="WorkflowStarted",
            invocation_id="invocation-1",
            engine="portable",
        )
    )
    metrics.observe_lifecycle(
        WorkflowEvent(
            event_type="TaskCompleted",
            invocation_id="invocation-1",
            engine="portable",
            status="completed",
            duration=0.02,
        )
    )
    metrics.observe_lifecycle(
        WorkflowEvent(
            event_type="WorkflowCompleted",
            invocation_id="invocation-1",
            engine="portable",
            status="completed",
            duration=0.02,
        )
    )
    metrics.observe_lifecycle(
        WorkflowEvent(
            event_type="SandboxExecutionStarted",
            invocation_id="invocation-1",
            engine="portable",
            status="running",
        )
    )
    metrics.observe_lifecycle(
        WorkflowEvent(
            event_type="SandboxExecutionCompleted",
            invocation_id="invocation-1",
            engine="portable",
            status="completed",
            duration=0.02,
        )
    )

    rendered = metrics.render()

    assert 'owa_workflow_started_total{engine="portable"} 1.0' in rendered
    assert 'owa_workflow_finished_total{engine="portable",status="completed"} 1.0' in rendered
    assert "owa_active_invocations 0.0" in rendered
    assert (
        'owa_task_events_total{engine="portable",event="TaskCompleted",status="completed"} 1.0'
        in rendered
    )
    assert (
        'owa_sandbox_executions_total{engine="portable",event="started",status="running"} 1.0'
        in rendered
    )
    assert 'owa_sandbox_duration_seconds_count{engine="portable",status="completed"} 1' in rendered
    assert (
        'owa_workflow_duration_seconds_bucket{engine="portable",status="completed",le="0.025"} 1'
        in rendered
    )
    assert (
        'owa_workflow_duration_seconds_bucket{engine="portable",status="completed",le="+Inf"} 1'
        in rendered
    )
    assert 'owa_workflow_duration_seconds_count{engine="portable",status="completed"} 1' in rendered


def test_metrics_endpoint_exposes_runtime_and_http_metrics(tmp_path) -> None:
    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}, "a2a": {"enabled": True}})
    services = RuntimeServices(config, database_root=tmp_path)
    app = create_app(config=config, services=services)

    with TestClient(app) as client:
        invoke = client.post("/v1/invoke", json={"input": "hello"})
        assert invoke.status_code == 200
        card = client.get("/.well-known/agent-card.json")
        assert card.status_code == 200
        a2a_error = client.get("/a2a")
        assert a2a_error.status_code == 405
        client.get("/metrics")
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    body = response.text
    assert "owa_workflow_started_total" in body
    assert "owa_workflow_finished_total" in body
    assert 'owa_http_requests_total{method="POST",status="200",surface="rest"}' in body
    assert 'owa_http_requests_total{method="GET",status="200",surface="a2a"}' in body
    assert 'owa_http_errors_total{method="GET",status="405",surface="a2a"}' in body
    assert 'owa_http_requests_total{method="GET",status="200",surface="metrics"}' in body
    assert 'owa_scheduler_jobs{status="active"} 0.0' in body
    assert "owa_approval_queue_depth 0.0" in body


def test_metrics_registry_tracks_traffic_policy_rejections() -> None:
    metrics = Metrics()
    metrics.record_traffic_rejection("rate_limit")
    metrics.set_traffic_active(2)

    rendered = metrics.render()

    assert 'owa_traffic_policy_rejections_total{reason="rate_limit"} 1.0' in rendered
    assert "owa_traffic_policy_active_requests 2.0" in rendered
