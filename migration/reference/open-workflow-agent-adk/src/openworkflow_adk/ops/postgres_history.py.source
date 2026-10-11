"""PostgreSQL-backed async run history store."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openworkflow_adk._utils import utc_now_iso
from openworkflow_adk.ops.history import RunRecord


def _to_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


@dataclass
class PostgresRunHistoryConfig:
    """Connection and schema configuration for the PostgreSQL backend."""

    url: str
    schema: str = "openworkflow"
    namespace_id: str = "default"
    run_migrations: bool = True
    min_pool_size: int = 1
    max_pool_size: int = 10


class PostgresRunHistory:
    """Durable async run history store backed by PostgreSQL.

    Mirrors the lifecycle surface of :class:`InMemoryRunHistory` and
    :class:`SQLiteRunHistory` so it can be dropped into ``run_workflow`` once
    the caller awaits async methods or the runtime bridges them.
    """

    def __init__(
        self, config: PostgresRunHistoryConfig | None = None, *, url: str | None = None
    ) -> None:
        if config is not None and url is not None:
            raise ValueError("provide config or url, not both")
        if config is None:
            if url is None:
                raise ValueError("provide config or url")
            config = PostgresRunHistoryConfig(url=url)
        self.config = config
        self._pool: Any | None = None

    async def _pool_ref(self) -> Any:
        import asyncpg

        current_loop = asyncio.get_running_loop()
        if self._pool is not None and getattr(self._pool, "_loop", None) is not current_loop:
            # Discarding the old pool avoids cross-loop close errors. In
            # production the history and its pool live on the same loop; this
            # branch mainly helps tests that spawn the app on another loop.
            self._pool = None
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self.config.url,
                min_size=self.config.min_pool_size,
                max_size=self.config.max_pool_size,
            )
        return self._pool

    async def connect(self) -> None:
        """Initialize the pool and run migrations when configured."""
        if self.config.run_migrations:
            await self._migrate()
        await self._pool_ref()

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def start(
        self, run_id: str, workflow: str, state: dict[str, Any], region: str | None = None
    ) -> RunRecord:
        pool = await self._pool_ref()
        now = utc_now_iso()
        record = RunRecord(
            run_id=run_id,
            workflow=workflow,
            status="running",
            started_at=now,
            state=dict(state),
            region=region,
        )
        await pool.execute(
            f"""
            INSERT INTO {self._table}.workflow_runs (
                namespace_id, run_id, workflow, status, started_at,
                state, event_log, checkpoint_index, region
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (namespace_id, run_id) DO UPDATE SET
                workflow = EXCLUDED.workflow,
                status = EXCLUDED.status,
                started_at = EXCLUDED.started_at,
                finished_at = EXCLUDED.finished_at,
                state = EXCLUDED.state,
                output = EXCLUDED.output,
                error = EXCLUDED.error,
                checkpoint_index = EXCLUDED.checkpoint_index,
                checkpoint_task = EXCLUDED.checkpoint_task,
                resume_at = EXCLUDED.resume_at,
                suspension_reason = EXCLUDED.suspension_reason,
                region = EXCLUDED.region,
                event_log = EXCLUDED.event_log,
                updated_at = NOW()
            """,
            self.config.namespace_id,
            run_id,
            workflow,
            record.status,
            _to_dt(record.started_at),
            json.dumps(record.state),
            json.dumps(record.event_log),
            record.checkpoint_index,
            region,
        )
        return record

    async def finish(
        self,
        run_id: str,
        *,
        state: dict[str, Any],
        output: Any = None,
        error: Exception | None = None,
    ) -> RunRecord:
        pool = await self._pool_ref()
        record = await self.get(run_id)
        record.status = "failed" if error else "completed"
        record.finished_at = utc_now_iso()
        record.state = dict(state)
        record.output = output
        record.error = str(error) if error else None
        await pool.execute(
            f"""
            UPDATE {self._table}.workflow_runs
            SET status = $1, finished_at = $2, state = $3, output = $4,
                error = $5, updated_at = NOW()
            WHERE namespace_id = $6 AND run_id = $7
            """,
            record.status,
            _to_dt(record.finished_at),
            json.dumps(record.state),
            json.dumps(record.output, default=str) if record.output is not None else None,
            record.error,
            self.config.namespace_id,
            run_id,
        )
        step_name = record.checkpoint_task or "workflow"
        await self.record_step_attempt(
            run_id,
            step_name,
            status=record.status,
            output=record.output,
            error=record.error,
        )
        return record

    async def checkpoint(
        self, run_id: str, *, state: dict[str, Any], index: int, task: str | None = None
    ) -> RunRecord:
        pool = await self._pool_ref()
        record = await self.get(run_id)
        record.state = dict(state)
        record.checkpoint_index = index
        record.checkpoint_task = task or record.checkpoint_task
        await pool.execute(
            f"""
            UPDATE {self._table}.workflow_runs
            SET state = $1, checkpoint_index = $2, checkpoint_task = $3,
                updated_at = NOW()
            WHERE namespace_id = $4 AND run_id = $5
            """,
            json.dumps(record.state),
            index,
            record.checkpoint_task,
            self.config.namespace_id,
            run_id,
        )
        if record.checkpoint_task:
            await self.record_step_attempt(run_id, record.checkpoint_task, status="running")
        return record

    async def get(self, run_id: str) -> RunRecord:
        pool = await self._pool_ref()
        row = await pool.fetchrow(
            f"""
            SELECT run_id, workflow, status, started_at, finished_at,
                   state, output, error, checkpoint_index, checkpoint_task,
                   resume_at, event_log, suspension_reason, region
            FROM {self._table}.workflow_runs
            WHERE namespace_id = $1 AND run_id = $2
            """,
            self.config.namespace_id,
            run_id,
        )
        if row is None:
            raise KeyError(run_id)
        return RunRecord(
            run_id=row["run_id"],
            workflow=row["workflow"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            state=json.loads(row["state"]),
            output=json.loads(row["output"]) if row["output"] is not None else None,
            error=row["error"],
            checkpoint_index=row["checkpoint_index"],
            checkpoint_task=row["checkpoint_task"],
            resume_at=row["resume_at"].isoformat() if row["resume_at"] is not None else None,
            event_log=json.loads(row["event_log"]),
            suspension_reason=row["suspension_reason"],
            region=row["region"],
        )

    async def suspend(
        self,
        run_id: str,
        *,
        state: dict[str, Any],
        index: int,
        task: str,
        resume_at: str,
        reason: str = "timer",
    ) -> RunRecord:
        pool = await self._pool_ref()
        record = await self.get(run_id)
        record.status = "suspended"
        record.state = dict(state)
        record.checkpoint_index = index
        record.checkpoint_task = task
        record.resume_at = resume_at
        record.suspension_reason = reason
        await pool.execute(
            f"""
            UPDATE {self._table}.workflow_runs
            SET status = $1, state = $2, checkpoint_index = $3,
                checkpoint_task = $4, resume_at = $5, suspension_reason = $6,
                updated_at = NOW()
            WHERE namespace_id = $7 AND run_id = $8
            """,
            record.status,
            json.dumps(record.state),
            index,
            task,
            _to_dt(resume_at),
            reason,
            self.config.namespace_id,
            run_id,
        )
        return record

    async def record_event(self, run_id: str, event: dict[str, Any]) -> RunRecord:
        """Atomically append an event to the run's JSONB event log.

        The append uses a single ``||`` concatenation inside the UPDATE so
        concurrent callers never lose events through a read-modify-write race,
        and the full log is not re-serialized per append.
        """
        pool = await self._pool_ref()
        await pool.execute(
            f"""
            UPDATE {self._table}.workflow_runs
            SET event_log = event_log || $1::jsonb, updated_at = NOW()
            WHERE namespace_id = $2 AND run_id = $3
            """,
            json.dumps([dict(event)], default=str),
            self.config.namespace_id,
            run_id,
        )
        return await self.get(run_id)

    async def record_step_attempt(
        self,
        run_id: str,
        step_name: str,
        *,
        status: str,
        output: Any = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a step attempt row for ``run_id``/``step_name``."""
        pool = await self._pool_ref()
        now = datetime.now(timezone.utc)
        row = await pool.fetchrow(
            f"""
            SELECT id, status FROM {self._table}.step_attempts
            WHERE namespace_id = $1 AND run_id = $2 AND step_name = $3
            ORDER BY created_at DESC LIMIT 1
            """,
            self.config.namespace_id,
            run_id,
            step_name,
        )
        if row is None:
            step_id = self._new_step_attempt_id()
            await pool.execute(
                f"""
                INSERT INTO {self._table}.step_attempts (
                    namespace_id, id, run_id, step_name, status,
                    output, error, started_at, finished_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                self.config.namespace_id,
                step_id,
                run_id,
                step_name,
                status,
                json.dumps(output) if output is not None else None,
                error,
                now if status == "running" else None,
                now if status != "running" else None,
            )
            return {"id": step_id, "status": status, "step_name": step_name}

        await pool.execute(
            f"""
            UPDATE {self._table}.step_attempts
            SET status = $1, output = $2, error = $3,
                finished_at = CASE WHEN $1 != 'running' THEN NOW() ELSE finished_at END,
                updated_at = NOW()
            WHERE namespace_id = $4 AND id = $5
            """,
            status,
            json.dumps(output) if output is not None else None,
            error,
            self.config.namespace_id,
            row["id"],
        )
        return {"id": row["id"], "status": status, "step_name": step_name}

    async def list_step_attempts(
        self,
        run_id: str,
        *,
        step_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return step attempts for a run, newest first."""
        pool = await self._pool_ref()
        conditions = ["namespace_id = $1", "run_id = $2"]
        args: list[Any] = [self.config.namespace_id, run_id]
        if step_name is not None:
            args.append(step_name)
            conditions.append(f"step_name = ${len(args)}")
        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT id, run_id, step_name, status, output, error,
                   started_at, finished_at, created_at, updated_at
            FROM {self._table}.step_attempts
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
        """
        args.extend([limit, offset])
        rows = await pool.fetch(query, *args)
        return [
            {
                "id": row["id"],
                "run_id": row["run_id"],
                "step_name": row["step_name"],
                "status": row["status"],
                "output": json.loads(row["output"]) if row["output"] is not None else None,
                "error": row["error"],
                "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
            }
            for row in rows
        ]

    @staticmethod
    def _new_step_attempt_id() -> str:
        import uuid

        return str(uuid.uuid4())

    async def list_runs(
        self,
        *,
        status: str | None = None,
        workflow: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunRecord]:
        pool = await self._pool_ref()
        conditions = ["namespace_id = $1"]
        args: list[Any] = [self.config.namespace_id]
        if status is not None:
            args.append(status)
            conditions.append(f"status = ${len(args)}")
        if workflow is not None:
            args.append(workflow)
            conditions.append(f"workflow = ${len(args)}")
        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT run_id, workflow, status, started_at, finished_at,
                   state, output, error, checkpoint_index, checkpoint_task,
                   resume_at, event_log, suspension_reason, region
            FROM {self._table}.workflow_runs
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
        """
        args.extend([limit, offset])
        rows = await pool.fetch(query, *args)
        return [self._record_from_row(row) for row in rows]

    async def count_by_status(self) -> dict[str, int]:
        pool = await self._pool_ref()
        rows = await pool.fetch(
            f"""
            SELECT status, COUNT(*) AS count
            FROM {self._table}.workflow_runs
            WHERE namespace_id = $1
            GROUP BY status
            """,
            self.config.namespace_id,
        )
        return {row["status"]: row["count"] for row in rows}

    async def enqueue_run(
        self,
        run_id: str,
        workflow: str,
        *,
        input: dict[str, Any] | None = None,
        available_at: datetime | None = None,
        workflow_namespace: str = "default",
    ) -> RunRecord:
        """Insert a run with status ``pending`` for workers to claim."""
        pool = await self._pool_ref()
        record = RunRecord(
            run_id=run_id,
            workflow=workflow,
            status="pending",
            started_at=utc_now_iso(),
            state=dict(input or {}),
        )
        await pool.execute(
            f"""
            INSERT INTO {self._table}.workflow_runs (
                namespace_id, run_id, workflow, workflow_namespace, status, started_at,
                state, event_log, checkpoint_index, available_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (namespace_id, run_id) DO UPDATE SET
                workflow = EXCLUDED.workflow,
                workflow_namespace = EXCLUDED.workflow_namespace,
                status = CASE
                    WHEN {self._table}.workflow_runs.status IN ('completed', 'failed', 'canceled')
                    THEN {self._table}.workflow_runs.status
                    ELSE EXCLUDED.status
                END,
                state = EXCLUDED.state,
                available_at = EXCLUDED.available_at,
                updated_at = NOW()
            """,
            self.config.namespace_id,
            run_id,
            workflow,
            workflow_namespace,
            record.status,
            _to_dt(record.started_at),
            json.dumps(record.state),
            json.dumps(record.event_log),
            record.checkpoint_index,
            available_at,
        )
        return record

    async def claim_run(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 30.0,
    ) -> RunRecord | None:
        """Atomically find and claim a runnable pending run."""
        from datetime import timedelta

        pool = await self._pool_ref()
        lease_interval = timedelta(seconds=lease_seconds)
        row = await pool.fetchrow(
            f"""
            WITH candidate AS (
                SELECT run_id
                FROM {self._table}.workflow_runs
                WHERE namespace_id = $1
                  AND status IN ('pending', 'running')
                  AND (available_at IS NULL OR available_at <= NOW())
                ORDER BY
                    CASE WHEN status = 'pending' THEN 0 ELSE 1 END,
                    available_at ASC NULLS FIRST,
                    created_at ASC,
                    run_id ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE {self._table}.workflow_runs AS wr
            SET status = 'running',
                worker_id = $2,
                available_at = NOW() + $3::interval,
                started_at = COALESCE(started_at, NOW()),
                updated_at = NOW()
            FROM candidate
            WHERE wr.namespace_id = $1 AND wr.run_id = candidate.run_id
            RETURNING wr.run_id, wr.workflow, wr.workflow_namespace, wr.status,
                      wr.started_at, wr.state, wr.event_log, wr.checkpoint_index,
                      wr.checkpoint_task, wr.region
            """,
            self.config.namespace_id,
            worker_id,
            lease_interval,
        )
        if row is None:
            return None
        return RunRecord(
            run_id=row["run_id"],
            workflow=row["workflow"],
            workflow_namespace=row.get("workflow_namespace") or "default",
            status=row["status"],
            started_at=row["started_at"].isoformat() if row["started_at"] else utc_now_iso(),
            state=json.loads(row["state"]),
            event_log=json.loads(row["event_log"]),
            checkpoint_index=row["checkpoint_index"],
            checkpoint_task=row["checkpoint_task"],
            region=row["region"],
        )

    async def extend_lease(
        self,
        run_id: str,
        worker_id: str,
        *,
        lease_seconds: float = 30.0,
    ) -> bool:
        """Extend the lease on a run owned by ``worker_id``."""
        from datetime import timedelta

        pool = await self._pool_ref()
        lease_interval = timedelta(seconds=lease_seconds)
        result = await pool.execute(
            f"""
            UPDATE {self._table}.workflow_runs
            SET available_at = NOW() + $1::interval, updated_at = NOW()
            WHERE namespace_id = $2 AND run_id = $3
              AND worker_id = $4
              AND status = 'running'
            """,
            lease_interval,
            self.config.namespace_id,
            run_id,
            worker_id,
        )
        return result.endswith("UPDATE 1")

    async def release_run(
        self,
        run_id: str,
        worker_id: str,
        *,
        status: str,
        output: Any = None,
        error: str | None = None,
    ) -> RunRecord:
        """Mark a claimed run terminal and clear the worker lease."""
        pool = await self._pool_ref()
        await pool.execute(
            f"""
            UPDATE {self._table}.workflow_runs
            SET status = $1,
                output = $2,
                error = $3,
                finished_at = NOW(),
                worker_id = NULL,
                available_at = NULL,
                updated_at = NOW()
            WHERE namespace_id = $4 AND run_id = $5 AND worker_id = $6
            """,
            status,
            json.dumps(output, default=str) if output is not None else None,
            error,
            self.config.namespace_id,
            run_id,
            worker_id,
        )
        return await self.get(run_id)

    async def stats_summary(
        self,
        *,
        workflow: str | None = None,
        workflow_namespace: str | None = None,
        status: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
    ) -> dict[str, Any]:
        """Return aggregate run counts and duration percentiles."""
        pool = await self._pool_ref()
        conditions = ["namespace_id = $1"]
        args: list[Any] = [self.config.namespace_id]
        if workflow is not None:
            args.append(workflow)
            conditions.append(f"workflow = ${len(args)}")
        if workflow_namespace is not None:
            args.append(workflow_namespace)
            conditions.append(f"workflow_namespace = ${len(args)}")
        if status is not None:
            args.append(status)
            conditions.append(f"status = ${len(args)}")
        if started_after is not None:
            args.append(started_after)
            conditions.append(f"started_at >= ${len(args)}")
        if started_before is not None:
            args.append(started_before)
            conditions.append(f"started_at <= ${len(args)}")
        where_clause = " AND ".join(conditions)

        count_row = await pool.fetchrow(
            f"""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                   COUNT(*) FILTER (WHERE status = 'failed') AS failed,
                   COUNT(*) FILTER (WHERE status = 'running') AS running,
                   COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                   COUNT(*) FILTER (WHERE status = 'suspended') AS suspended,
                   COUNT(*) FILTER (WHERE status = 'canceled') AS canceled
            FROM {self._table}.workflow_runs
            WHERE {where_clause}
            """,
            *args,
        )
        duration_row = await pool.fetchrow(
            f"""
            SELECT
                PERCENTILE_CONT(0.50) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (finished_at - started_at))
                ) AS p50,
                PERCENTILE_CONT(0.95) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (finished_at - started_at))
                ) AS p95,
                PERCENTILE_CONT(0.99) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (finished_at - started_at))
                ) AS p99
            FROM {self._table}.workflow_runs
            WHERE {where_clause}
              AND status = 'completed'
              AND started_at IS NOT NULL
              AND finished_at IS NOT NULL
            """,
            *args,
        )
        return {
            "total": count_row["total"] if count_row else 0,
            "by_status": {
                "completed": count_row["completed"] if count_row else 0,
                "failed": count_row["failed"] if count_row else 0,
                "running": count_row["running"] if count_row else 0,
                "pending": count_row["pending"] if count_row else 0,
                "suspended": count_row["suspended"] if count_row else 0,
                "canceled": count_row["canceled"] if count_row else 0,
            },
            "duration_seconds": {
                "p50": float(duration_row["p50"]) if duration_row and duration_row["p50"] else None,
                "p95": float(duration_row["p95"]) if duration_row and duration_row["p95"] else None,
                "p99": float(duration_row["p99"]) if duration_row and duration_row["p99"] else None,
            },
        }

    async def failure_summary(
        self,
        *,
        workflow: str | None = None,
        workflow_namespace: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return recent failed runs with error text."""
        pool = await self._pool_ref()
        conditions = ["namespace_id = $1", "status = 'failed'"]
        args: list[Any] = [self.config.namespace_id]
        if workflow is not None:
            args.append(workflow)
            conditions.append(f"workflow = ${len(args)}")
        if workflow_namespace is not None:
            args.append(workflow_namespace)
            conditions.append(f"workflow_namespace = ${len(args)}")
        if started_after is not None:
            args.append(started_after)
            conditions.append(f"started_at >= ${len(args)}")
        if started_before is not None:
            args.append(started_before)
            conditions.append(f"started_at <= ${len(args)}")
        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT run_id, workflow, error, started_at, finished_at
            FROM {self._table}.workflow_runs
            WHERE {where_clause}
            ORDER BY finished_at DESC NULLS LAST
            LIMIT ${len(args) + 1}
        """
        args.append(limit)
        rows = await pool.fetch(query, *args)
        return [
            {
                "run_id": row["run_id"],
                "workflow": row["workflow"],
                "error": row["error"],
                "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
            }
            for row in rows
        ]

    def _record_from_row(self, row: Any) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            workflow=row["workflow"],
            workflow_namespace=row.get("workflow_namespace") or "default",
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            state=json.loads(row["state"]),
            output=json.loads(row["output"]) if row["output"] is not None else None,
            error=row["error"],
            checkpoint_index=row["checkpoint_index"],
            checkpoint_task=row["checkpoint_task"],
            resume_at=row["resume_at"].isoformat() if row["resume_at"] is not None else None,
            event_log=json.loads(row["event_log"]),
            suspension_reason=row["suspension_reason"],
            region=row["region"],
        )

    @property
    def _table(self) -> str:
        return self._quote_identifier(self.config.schema)

    async def _migrate(self) -> None:

        pool = await self._pool_ref()
        schema = self._quote_identifier(self.config.schema)
        async with pool.acquire() as connection:
            await connection.execute(
                f"""
                CREATE SCHEMA IF NOT EXISTS {schema}
                """
            )
            await connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {schema}.openworkflow_migrations (
                    version BIGINT NOT NULL PRIMARY KEY
                )
                """
            )
            version = await connection.fetchval(
                f"""
                SELECT version FROM {schema}.openworkflow_migrations
                ORDER BY version DESC LIMIT 1
                """
            )
            version = version or 0
            for migration_version, sql in enumerate(self._migrations(self.config.schema), start=1):
                if migration_version <= version:
                    continue
                await connection.execute(sql)
                await connection.execute(
                    f"""
                    INSERT INTO {schema}.openworkflow_migrations (version)
                    VALUES ($1)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    migration_version,
                )

    def _migrations(self, schema: str) -> list[str]:
        quoted = self._quote_identifier(schema)
        return [
            f"""
            BEGIN;
            CREATE TABLE IF NOT EXISTS {quoted}.workflow_runs (
                namespace_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                workflow TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TIMESTAMPTZ NOT NULL,
                finished_at TIMESTAMPTZ,
                state JSONB NOT NULL,
                output JSONB,
                error TEXT,
                checkpoint_index INTEGER NOT NULL DEFAULT 0,
                checkpoint_task TEXT,
                resume_at TIMESTAMPTZ,
                suspension_reason TEXT,
                region TEXT,
                worker_id TEXT,
                available_at TIMESTAMPTZ,
                workflow_namespace TEXT NOT NULL DEFAULT 'default',
                event_log JSONB NOT NULL DEFAULT '[]',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (namespace_id, run_id)
            );
            CREATE INDEX IF NOT EXISTS workflow_runs_status_created_at_idx
                ON {quoted}.workflow_runs (namespace_id, status, created_at DESC);
            CREATE INDEX IF NOT EXISTS workflow_runs_workflow_created_at_idx
                ON {quoted}.workflow_runs (namespace_id, workflow, created_at DESC);
            COMMIT;
            """,
            f"""
            BEGIN;
            CREATE TABLE IF NOT EXISTS {quoted}.step_attempts (
                namespace_id TEXT NOT NULL,
                id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                step_name TEXT NOT NULL,
                status TEXT NOT NULL,
                output JSONB,
                error TEXT,
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (namespace_id, id),
                FOREIGN KEY (namespace_id, run_id)
                    REFERENCES {quoted}.workflow_runs (namespace_id, run_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS step_attempts_run_created_at_idx
                ON {quoted}.step_attempts (namespace_id, run_id, created_at);
            CREATE INDEX IF NOT EXISTS step_attempts_run_step_idx
                ON {quoted}.step_attempts (namespace_id, run_id, step_name, created_at);
            COMMIT;
            """,
            f"""
            BEGIN;
            ALTER TABLE {quoted}.workflow_runs
                ADD COLUMN IF NOT EXISTS worker_id TEXT,
                ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS workflow_namespace TEXT NOT NULL DEFAULT 'default';
            CREATE INDEX IF NOT EXISTS workflow_runs_available_at_idx
                ON {quoted}.workflow_runs (namespace_id, status, available_at, created_at);
            COMMIT;
            """,
            f"""
            BEGIN;
            CREATE INDEX IF NOT EXISTS workflow_runs_namespace_created_at_idx
                ON {quoted}.workflow_runs (namespace_id, created_at DESC);
            COMMIT;
            """,
        ]

    @staticmethod
    def _quote_identifier(name: str) -> str:
        if not name:
            raise ValueError("identifier must be non-empty")
        if not name[0].isalpha() and name[0] != "_":
            raise ValueError(f"invalid identifier: {name!r}")
        if not all(c.isalnum() or c == "_" for c in name):
            raise ValueError(f"invalid identifier: {name!r}")
        return f'"{name}"'
