"""Run history stores for workflow inspection."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any

from openworkflow_adk._utils import utc_now_iso


@dataclass
class RunRecord:
    run_id: str
    workflow: str
    status: str = "running"
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    error: str | None = None
    checkpoint_index: int = 0
    checkpoint_task: str | None = None
    resume_at: str | None = None
    suspension_reason: str | None = None
    region: str | None = None
    workflow_namespace: str = "default"
    event_log: list[dict[str, Any]] = field(default_factory=list)


class InMemoryRunHistory:
    """Small run store suitable for development and tests."""

    def __init__(self) -> None:
        self.records: dict[str, RunRecord] = {}

    def start(
        self, run_id: str, workflow: str, state: dict[str, Any], region: str | None = None
    ) -> RunRecord:
        record = RunRecord(run_id=run_id, workflow=workflow, state=dict(state), region=region)
        self.records[run_id] = record
        return record

    def finish(
        self,
        run_id: str,
        *,
        state: dict[str, Any],
        output: Any = None,
        error: Exception | None = None,
    ) -> RunRecord:
        record = self.records[run_id]
        record.status = "failed" if error else "completed"
        record.finished_at = utc_now_iso()
        record.state = dict(state)
        record.output = output
        record.error = str(error) if error else None
        return record

    def checkpoint(
        self, run_id: str, *, state: dict[str, Any], index: int, task: str | None = None
    ) -> RunRecord:
        record = self.records[run_id]
        record.state = dict(state)
        record.checkpoint_index = index
        record.checkpoint_task = task or record.checkpoint_task
        return record

    def get(self, run_id: str) -> RunRecord:
        return self.records[run_id]

    def suspend(
        self,
        run_id: str,
        *,
        state: dict[str, Any],
        index: int,
        task: str,
        resume_at: str,
        reason: str = "timer",
    ) -> RunRecord:
        record = self.records[run_id]
        record.status = "suspended"
        record.state = dict(state)
        record.checkpoint_index = index
        record.checkpoint_task = task
        record.resume_at = resume_at
        record.suspension_reason = reason
        return record

    def record_event(self, run_id: str, event: dict[str, Any]) -> RunRecord:
        record = self.records[run_id]
        record.event_log.append(dict(event))
        return record


class SQLiteRunHistory:
    """Persistent run store using a single SQLite table."""

    def __init__(self, path: str) -> None:
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    workflow TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    state TEXT NOT NULL,
                    output TEXT,
                    error TEXT,
                    checkpoint_index INTEGER NOT NULL DEFAULT 0,
                    checkpoint_task TEXT,
                    resume_at TEXT
                    ,event_log TEXT NOT NULL DEFAULT '[]',
                    suspension_reason TEXT,
                    region TEXT
                )"""
            )
            columns = {
                row[1] for row in self.connection.execute("PRAGMA table_info(workflow_runs)")
            }
            if "checkpoint_index" not in columns:
                self.connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN checkpoint_index "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if "checkpoint_task" not in columns:
                self.connection.execute("ALTER TABLE workflow_runs ADD COLUMN checkpoint_task TEXT")
            if "resume_at" not in columns:
                self.connection.execute("ALTER TABLE workflow_runs ADD COLUMN resume_at TEXT")
            if "event_log" not in columns:
                self.connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN event_log TEXT NOT NULL DEFAULT '[]'"
                )
            if "suspension_reason" not in columns:
                self.connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN suspension_reason TEXT"
                )
            if "region" not in columns:
                self.connection.execute("ALTER TABLE workflow_runs ADD COLUMN region TEXT")
            self.connection.commit()

    def start(
        self, run_id: str, workflow: str, state: dict[str, Any], region: str | None = None
    ) -> RunRecord:
        record = RunRecord(run_id=run_id, workflow=workflow, state=dict(state), region=region)
        with self._lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO workflow_runs
                (run_id, workflow, status, started_at, finished_at, state, output, error,
                 checkpoint_index, checkpoint_task, resume_at, event_log, suspension_reason, region)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.run_id,
                    record.workflow,
                    record.status,
                    record.started_at,
                    None,
                    json.dumps(record.state),
                    None,
                    None,
                    0,
                    None,
                    None,
                    "[]",
                    None,
                    region,
                ),
            )
            self.connection.commit()
        return record

    def finish(
        self,
        run_id: str,
        *,
        state: dict[str, Any],
        output: Any = None,
        error: Exception | None = None,
    ) -> RunRecord:
        record = self.get(run_id)
        record.status = "failed" if error else "completed"
        record.finished_at = utc_now_iso()
        record.state = dict(state)
        record.output = output
        record.error = str(error) if error else None
        with self._lock:
            self.connection.execute(
                "UPDATE workflow_runs SET status=?, finished_at=?, state=?, output=?, error=? "
                "WHERE run_id=?",
                (
                    record.status,
                    record.finished_at,
                    json.dumps(record.state),
                    json.dumps(record.output),
                    record.error,
                    run_id,
                ),
            )
            self.connection.commit()
        return record

    def checkpoint(
        self, run_id: str, *, state: dict[str, Any], index: int, task: str | None = None
    ) -> RunRecord:
        record = self.get(run_id)
        record.state = dict(state)
        record.checkpoint_index = index
        record.checkpoint_task = task or record.checkpoint_task
        with self._lock:
            self.connection.execute(
                "UPDATE workflow_runs SET state=?, checkpoint_index=?, checkpoint_task=? "
                "WHERE run_id=?",
                (json.dumps(record.state), index, record.checkpoint_task, run_id),
            )
            self.connection.commit()
        return record

    def get(self, run_id: str) -> RunRecord:
        with self._lock:
            row = self.connection.execute(
                "SELECT run_id, workflow, status, started_at, finished_at, state, output, error, "
                "checkpoint_index, checkpoint_task, resume_at, event_log, "
                "suspension_reason, region FROM workflow_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunRecord(
            run_id=row[0],
            workflow=row[1],
            status=row[2],
            started_at=row[3],
            finished_at=row[4],
            state=json.loads(row[5]),
            output=json.loads(row[6]) if row[6] is not None else None,
            error=row[7],
            checkpoint_index=row[8],
            checkpoint_task=row[9],
            resume_at=row[10],
            event_log=json.loads(row[11]),
            suspension_reason=row[12],
            region=row[13],
        )

    def suspend(
        self,
        run_id: str,
        *,
        state: dict[str, Any],
        index: int,
        task: str,
        resume_at: str,
        reason: str = "timer",
    ) -> RunRecord:
        record = self.get(run_id)
        record.status = "suspended"
        record.state = dict(state)
        record.checkpoint_index = index
        record.checkpoint_task = task
        record.resume_at = resume_at
        record.suspension_reason = reason
        with self._lock:
            self.connection.execute(
                "UPDATE workflow_runs SET status=?, state=?, checkpoint_index=?, "
                "checkpoint_task=?, resume_at=?, suspension_reason=? WHERE run_id=?",
                (record.status, json.dumps(record.state), index, task, resume_at, reason, run_id),
            )
            self.connection.commit()
        return record

    def record_event(self, run_id: str, event: dict[str, Any]) -> RunRecord:
        record = self.get(run_id)
        record.event_log.append(dict(event))
        with self._lock:
            self.connection.execute(
                "UPDATE workflow_runs SET event_log=? WHERE run_id=?",
                (json.dumps(record.event_log, default=str), run_id),
            )
            self.connection.commit()
        return record

    def close(self) -> None:
        with self._lock:
            self.connection.close()
