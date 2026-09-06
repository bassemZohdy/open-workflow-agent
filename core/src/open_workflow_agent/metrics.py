"""Bounded Prometheus metrics for the common runtime surface."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .observability import EventSink, WorkflowEvent

_HISTOGRAM_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_HELP = {
    "owa_workflow_started_total": "Workflow executions started.",
    "owa_workflow_finished_total": "Workflow executions finished by outcome.",
    "owa_workflow_duration_seconds": "Workflow execution duration in seconds.",
    "owa_active_invocations": "Invocations currently running or waiting.",
    "owa_task_events_total": "Workflow task lifecycle events.",
    "owa_sandbox_executions_total": "Sandbox executions by lifecycle event and outcome.",
    "owa_sandbox_duration_seconds": "Sandbox execution duration in seconds.",
    "owa_http_requests_total": "HTTP requests received by runtime surface.",
    "owa_http_errors_total": "HTTP requests returning an error status.",
    "owa_http_request_duration_seconds": "HTTP request duration in seconds.",
    "owa_traffic_policy_rejections_total": "Requests rejected by traffic policy.",
    "owa_traffic_policy_active_requests": "Requests currently admitted by traffic policy.",
    "owa_scheduler_jobs": "Persisted scheduler jobs by status.",
    "owa_approval_queue_depth": "Pending durable approvals.",
}


@dataclass(slots=True)
class _Histogram:
    counts: list[int]
    total_count: int = 0
    total_sum: float = 0.0

    @classmethod
    def create(cls) -> _Histogram:
        return cls(counts=[0] * len(_HISTOGRAM_BUCKETS))


def _labels(labels: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _label_text(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in labels) + "}"


def _sample(name: str, labels: tuple[tuple[str, str], ...], value: float | int) -> str:
    return f"{name}{_label_text(labels)} {value}"


class Metrics:
    """Thread-safe in-process metrics with bounded label dimensions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = {
            "owa_active_invocations": {(): 0.0},
            "owa_traffic_policy_active_requests": {(): 0.0},
        }
        self._histograms: dict[str, dict[tuple[tuple[str, str], ...], _Histogram]] = {}
        self._active_invocations: set[str] = set()

    def increment(
        self, name: str, amount: float = 1.0, labels: Mapping[str, Any] | None = None
    ) -> None:
        key = _labels(labels)
        with self._lock:
            values = self._counters.setdefault(name, {})
            values[key] = values.get(key, 0.0) + amount

    def set_gauge(self, name: str, value: float, labels: Mapping[str, Any] | None = None) -> None:
        key = _labels(labels)
        with self._lock:
            self._gauges.setdefault(name, {})[key] = value

    def observe(self, name: str, value: float, labels: Mapping[str, Any] | None = None) -> None:
        key = _labels(labels)
        with self._lock:
            histogram = self._histograms.setdefault(name, {}).setdefault(key, _Histogram.create())
            histogram.total_count += 1
            histogram.total_sum += value
            for index, bucket in enumerate(_HISTOGRAM_BUCKETS):
                if value <= bucket:
                    histogram.counts[index] += 1

    def observe_http(self, *, surface: str, method: str, status_code: int, duration: float) -> None:
        labels = {"surface": surface, "method": method, "status": str(status_code)}
        self.increment("owa_http_requests_total", labels=labels)
        if status_code >= 400:
            self.increment("owa_http_errors_total", labels=labels)
        self.observe(
            "owa_http_request_duration_seconds",
            duration,
            labels={"surface": surface},
        )

    def observe_lifecycle(self, event: WorkflowEvent) -> None:
        engine = event.engine or "unknown"
        if event.event_type == "WorkflowStarted":
            self.increment("owa_workflow_started_total", labels={"engine": engine})
            if event.invocation_id is not None:
                with self._lock:
                    self._active_invocations.add(event.invocation_id)
                    self._set_active_gauge_locked()
        elif event.event_type in {
            "WorkflowCompleted",
            "WorkflowFaulted",
            "WorkflowCancelled",
        }:
            status = (
                event.status
                or {
                    "WorkflowCompleted": "completed",
                    "WorkflowFaulted": "faulted",
                    "WorkflowCancelled": "cancelled",
                }[event.event_type]
            )
            self.increment(
                "owa_workflow_finished_total",
                labels={"engine": engine, "status": status},
            )
            if event.duration is not None:
                self.observe(
                    "owa_workflow_duration_seconds",
                    max(0.0, event.duration),
                    labels={"engine": engine, "status": status},
                )
            if event.invocation_id is not None:
                with self._lock:
                    self._active_invocations.discard(event.invocation_id)
                    self._set_active_gauge_locked()

        if event.event_type.startswith("Task"):
            self.increment(
                "owa_task_events_total",
                labels={
                    "engine": engine,
                    "event": event.event_type,
                    "status": event.status or "unknown",
                },
            )

        if event.event_type.startswith("SandboxExecution"):
            action = event.event_type.removeprefix("SandboxExecution").lower()
            self.increment(
                "owa_sandbox_executions_total",
                labels={"engine": engine, "event": action, "status": event.status or "unknown"},
            )
            if action in {"completed", "failed", "cancelled"} and event.duration is not None:
                self.observe(
                    "owa_sandbox_duration_seconds",
                    max(0.0, event.duration),
                    labels={"engine": engine, "status": event.status or action},
                )

    def record_traffic_rejection(self, reason: str) -> None:
        self.increment("owa_traffic_policy_rejections_total", labels={"reason": reason})

    def set_traffic_active(self, value: int) -> None:
        self.set_gauge("owa_traffic_policy_active_requests", float(value))

    def _set_active_gauge_locked(self) -> None:
        self._gauges.setdefault("owa_active_invocations", {})[()] = float(
            len(self._active_invocations)
        )

    def render(self) -> str:
        """Render the current registry using Prometheus text format 0.0.4."""

        lines: list[str] = []
        with self._lock:
            names = set(self._counters) | set(self._gauges) | set(self._histograms)
            for name in sorted(names):
                lines.append(f"# HELP {name} {_HELP.get(name, name)}")
                metric_type = (
                    "histogram"
                    if name in self._histograms
                    else ("counter" if name in self._counters else "gauge")
                )
                lines.append(f"# TYPE {name} {metric_type}")
                for key, value in sorted(self._counters.get(name, {}).items()):
                    lines.append(_sample(name, key, value))
                for key, value in sorted(self._gauges.get(name, {}).items()):
                    lines.append(_sample(name, key, value))
                for key, histogram in sorted(self._histograms.get(name, {}).items()):
                    for bucket, count in zip(_HISTOGRAM_BUCKETS, histogram.counts, strict=True):
                        bucket_labels = (*key, ("le", str(bucket)))
                        lines.append(_sample(f"{name}_bucket", bucket_labels, count))
                    lines.append(
                        _sample(
                            f"{name}_bucket",
                            (*key, ("le", "+Inf")),
                            histogram.total_count,
                        )
                    )
                    lines.append(_sample(f"{name}_count", key, histogram.total_count))
                    lines.append(_sample(f"{name}_sum", key, histogram.total_sum))
        return "\n".join(lines) + ("\n" if lines else "")


class MetricsEventSink:
    """Observe lifecycle events while preserving the configured event sink."""

    def __init__(self, downstream: EventSink, metrics: Metrics) -> None:
        self.downstream = downstream
        self.metrics = metrics

    def emit(self, event: WorkflowEvent) -> None:
        self.metrics.observe_lifecycle(event)
        self.downstream.emit(event)


class MetricsMiddleware:
    """Record bounded HTTP request metrics without exposing request data."""

    def __init__(self, app: Any, *, metrics: Metrics, a2a_path: str) -> None:
        self.app = app
        self.metrics = metrics
        self.a2a_path = a2a_path.rstrip("/") or "/a2a"

    def _surface(self, path: str) -> str:
        if path == "/metrics":
            return "metrics"
        if path.startswith("/health/"):
            return "health"
        if (
            path == "/.well-known/agent-card.json"
            or path == self.a2a_path
            or path.startswith(f"{self.a2a_path}/")
        ):
            return "a2a"
        if path.startswith("/v1/"):
            return "rest"
        return "other"

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        surface = self._surface(str(scope.get("path", "")))
        method = str(scope.get("method", "GET"))
        status_code = 500
        recorded = False

        def record() -> None:
            nonlocal recorded
            if not recorded:
                recorded = True
                self.metrics.observe_http(
                    surface=surface,
                    method=method,
                    status_code=status_code,
                    duration=max(0.0, time.perf_counter() - started),
                )

        async def send_with_metrics(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                record()
            await send(message)

        try:
            await self.app(scope, receive, send_with_metrics)
        except Exception:
            record()
            raise
        finally:
            record()
