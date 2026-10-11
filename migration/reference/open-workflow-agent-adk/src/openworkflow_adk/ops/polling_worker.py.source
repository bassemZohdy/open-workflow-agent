"""Database-polling workflow worker using PostgresRunHistory as the queue."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from openworkflow_adk.ops.postgres_history import PostgresRunHistory
from openworkflow_adk.registry import WorkflowRegistry
from openworkflow_adk.runtime import run_workflow


class PostgresPollingWorker:
    """Poll a PostgreSQL run-history store for pending runs and execute them.

    This is an alternative to the broker-driven :class:`WorkflowWorker`. It
    relies on ``PostgresRunHistory`` atomic ``claim_run`` semantics so multiple
    worker processes can coordinate through the database.
    """

    def __init__(
        self,
        registry: WorkflowRegistry,
        history: PostgresRunHistory,
        *,
        max_concurrency: int = 4,
        poll_interval_seconds: float = 1.0,
        lease_seconds: float = 30.0,
        heartbeat_interval_seconds: float = 10.0,
        region: str | None = None,
        on_result: Callable[[str, list[Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.registry = registry
        self.history = history
        self.max_concurrency = max_concurrency
        self.poll_interval_seconds = poll_interval_seconds
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.region = region
        self.on_result = on_result
        self._worker_id = str(uuid.uuid4())
        self._running = False
        self._tasks: set[asyncio.Task[Any]] = set()
        self._heartbeats: dict[str, asyncio.Task[Any]] = {}

    async def run_once(self) -> list[Any] | None:
        """Claim and execute one pending run, or return None if none available."""
        claimed = await self.history.claim_run(self._worker_id, lease_seconds=self.lease_seconds)
        if claimed is None:
            return None
        return await self._execute_claimed(claimed)

    async def _execute_claimed(self, claimed) -> list[Any]:
        run_id = claimed.run_id
        document = self.registry.resolve(
            namespace=claimed.workflow_namespace,
            name=claimed.workflow,
            version="latest",
        )
        heartbeat = asyncio.create_task(self._heartbeat_loop(run_id))
        self._heartbeats[run_id] = heartbeat
        try:
            result = await run_workflow(
                document,
                claimed.state,
                history=self.history,
                session_id=run_id,
                region=self.region,
            )
        except Exception as exc:
            logging.getLogger(__name__).exception(
                "PostgresPollingWorker execution failed for run %s: %s", run_id, exc
            )
            try:
                await self.history.release_run(
                    run_id, self._worker_id, status="failed", error=str(exc)
                )
            except Exception:
                logging.getLogger(__name__).exception("Failed to release failed run %s", run_id)
            raise
        else:
            await self.history.release_run(
                run_id, self._worker_id, status="completed", output=result
            )
            if self.on_result is not None:
                delivered = self.on_result(run_id, result)
                if hasattr(delivered, "__await__"):
                    await delivered
            return result
        finally:
            heartbeat.cancel()
            self._heartbeats.pop(run_id, None)

    async def _heartbeat_loop(self, run_id: str) -> None:
        while True:
            try:
                await asyncio.sleep(self.heartbeat_interval_seconds)
                extended = await self.history.extend_lease(
                    run_id, self._worker_id, lease_seconds=self.lease_seconds
                )
                if not extended:
                    logging.getLogger(__name__).warning(
                        "Lease for run %s no longer owned by worker %s", run_id, self._worker_id
                    )
                    return
            except asyncio.CancelledError:
                return
            except Exception:
                logging.getLogger(__name__).exception("Heartbeat failed for run %s", run_id)

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        """Poll and execute runs until ``stop`` is set."""
        self._running = True
        logger = logging.getLogger(__name__)
        try:
            while self._running and (stop is None or not stop.is_set()):
                while len(self._tasks) >= self.max_concurrency:
                    done, self._tasks = await asyncio.wait(
                        self._tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in done:
                        try:
                            await task
                        except Exception as exc:
                            logger.exception("PostgresPollingWorker task failed: %s", exc)

                task = asyncio.create_task(self.run_once())
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

                await asyncio.sleep(self.poll_interval_seconds)
        finally:
            self._running = False
            for task in self._tasks:
                task.cancel()
            for heartbeat in self._heartbeats.values():
                heartbeat.cancel()
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
            if self._heartbeats:
                await asyncio.gather(*self._heartbeats.values(), return_exceptions=True)

    async def stop(self) -> None:
        """Signal the worker to stop."""
        self._running = False
