"""Broker-driven workflow worker for horizontally scalable execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from openworkflow_adk.ops.backpressure import BackpressureController
from openworkflow_adk.ops.history import InMemoryRunHistory, SQLiteRunHistory
from openworkflow_adk.registry import WorkflowRegistry
from openworkflow_adk.resources.broker import Broker
from openworkflow_adk.runtime import run_workflow


class WorkflowWorker:
    """Consume workflow job events and execute them with bounded concurrency."""

    def __init__(
        self,
        registry: WorkflowRegistry,
        broker: Broker,
        *,
        history: InMemoryRunHistory | SQLiteRunHistory | None = None,
        max_concurrency: int = 8,
        region: str | None = None,
        backpressure: BackpressureController | None = None,
        on_result: Callable[[str, list[Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.registry = registry
        self.broker = broker
        self.history = history
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.region = region
        self.backpressure = backpressure
        self.on_result = on_result

    async def run_once(self) -> list[Any]:
        """Consume and execute one ``workflow.run`` event."""
        event = await self.broker.consume(event_type="workflow.run")
        data = event.get("data") if isinstance(event.get("data"), dict) else event
        namespace = str(data.get("namespace", ""))
        name = str(data.get("name", ""))
        version = str(data.get("version", "latest"))
        run_id = str(data.get("run_id") or f"worker-{id(event)}")
        job_region = data.get("region")
        if self.region is not None and job_region is not None and job_region != self.region:
            raise RuntimeError(
                f"workflow job region {job_region!r} does not match worker region {self.region!r}"
            )
        document = self.registry.resolve(namespace, name, version)
        endpoint = str(data.get("endpoint") or f"{namespace}/{name}")
        if self.backpressure is not None:
            await self.backpressure.acquire(endpoint)
        async with self.semaphore:
            try:
                result = await run_workflow(
                    document,
                    data.get("input") if isinstance(data.get("input"), dict) else {},
                    broker=self.broker,
                    history=self.history,
                    session_id=run_id,
                    region=self.region,
                )
            finally:
                if self.backpressure is not None:
                    self.backpressure.release(endpoint)
        if self.on_result is not None:
            delivered = self.on_result(run_id, result)
            if hasattr(delivered, "__await__"):
                await delivered
        return result

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        """Process jobs until ``stop`` is set, with backoff on transient errors."""
        logger = logging.getLogger(__name__)
        backoff_seconds = 1.0
        while stop is None or not stop.is_set():
            try:
                await self.run_once()
                backoff_seconds = 1.0
            except Exception as exc:  # noqa: BLE001
                logger.exception("WorkflowWorker run_once failed: %s", exc)
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 60.0)
