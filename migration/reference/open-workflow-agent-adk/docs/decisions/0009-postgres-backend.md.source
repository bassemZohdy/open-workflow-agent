# ADR 0009: PostgreSQL execution backend

## Status

Accepted — design aligned with upstream OpenWorkflow PostgreSQL backend.

## Context

The upstream [OpenWorkflow](https://openworkflow.dev/docs/postgres) reference
implementation uses a PostgreSQL backend for durable workflow execution state
and statistics. The project already has `InMemoryRunHistory` and
`SQLiteRunHistory`, plus an asyncpg runtime dependency and
testcontainers-postgres in dev extras. To reach parity with the upstream
reference, we need a PostgreSQL-backed execution store that supports the same
run/step lifecycle, query patterns, and observability surface.

This ADR documents the upstream schema audit and the ADK-aligned design.

## Upstream audit

Sources: [PostgreSQL Backend](https://openworkflow.dev/docs/postgres) and the
upstream TypeScript source (`packages/openworkflow/postgres/`).

### Configuration

- Connection URL: standard `postgresql://` format.
- Options:
  - `namespaceId` (default `"default"`) — tenant/environment isolation.
  - `schema` (default `"openworkflow"`) — database schema for all tables.
  - `runMigrations` (default `true`) — auto-run migrations on connect.
- Requirements: PostgreSQL 14+; connecting user needs schema/table create
  permissions when migrations are enabled.

### Schema

Migrations create the configured schema and three tables:

#### `workflow_runs`

Stores a single workflow execution instance.

| Column | Type | Notes |
| --- | --- | --- |
| `namespace_id` | `TEXT NOT NULL` | Part of primary key with `id`. |
| `id` | `TEXT NOT NULL` | `gen_random_uuid()` generated. |
| `workflow_name` | `TEXT NOT NULL` | Workflow definition name. |
| `version` | `TEXT` | Workflow version, nullable. |
| `status` | `TEXT NOT NULL` | `pending`, `running`, `sleeping` (deprecated), `succeeded` (deprecated), `completed`, `failed`, `canceled`. |
| `idempotency_key` | `TEXT` | Optional idempotency key. |
| `config` | `JSONB NOT NULL` | User-defined config object. |
| `context` | `JSONB` | Runtime execution metadata. |
| `input` | `JSONB` | Workflow input. |
| `output` | `JSONB` | Workflow output when terminal. |
| `error` | `JSONB` | Serialized error when failed. |
| `attempts` | `INTEGER NOT NULL` | Number of claim/execution attempts. |
| `parent_step_attempt_namespace_id` | `TEXT` | For child workflows. |
| `parent_step_attempt_id` | `TEXT` | For child workflows. |
| `worker_id` | `TEXT` | Worker that currently owns the run. |
| `available_at` | `TIMESTAMPTZ` | When the run becomes claimable; also used as lease expiry while running. |
| `deadline_at` | `TIMESTAMPTZ` | Overall run deadline. |
| `started_at` | `TIMESTAMPTZ` | First claim time. |
| `finished_at` | `TIMESTAMPTZ` | Terminal transition time. |
| `created_at` | `TIMESTAMPTZ NOT NULL` | Run creation time. |
| `updated_at` | `TIMESTAMPTZ NOT NULL` | Last mutation time. |

Primary key: `(namespace_id, id)`.

#### `step_attempts`

Stores every attempt of every step within a run.

| Column | Type | Notes |
| --- | --- | --- |
| `namespace_id` | `TEXT NOT NULL` | Part of primary key with `id`. |
| `id` | `TEXT NOT NULL` | `gen_random_uuid()` generated. |
| `workflow_run_id` | `TEXT NOT NULL` | FK to `workflow_runs`. |
| `step_name` | `TEXT NOT NULL` | Step identifier. |
| `kind` | `TEXT NOT NULL` | `function`, `sleep`, `workflow`, `signal-send`, `signal-wait`. |
| `status` | `TEXT NOT NULL` | `running`, `succeeded` (deprecated), `completed`, `failed`. |
| `config` | `JSONB NOT NULL` | Step config. |
| `context` | `JSONB` | Runtime context (sleep resume time, signal name, etc.). |
| `output` | `JSONB` | Step output. |
| `error` | `JSONB` | Step error. |
| `child_workflow_run_namespace_id` | `TEXT` | For nested workflow steps. |
| `child_workflow_run_id` | `TEXT` | For nested workflow steps. |
| `started_at` | `TIMESTAMPTZ` | Execution start. |
| `finished_at` | `TIMESTAMPTZ` | Execution finish. |
| `created_at` | `TIMESTAMPTZ NOT NULL` | Attempt creation. |
| `updated_at` | `TIMESTAMPTZ NOT NULL` | Last mutation. |

Primary key: `(namespace_id, id)`.
Foreign keys:
- `(namespace_id, workflow_run_id)` → `workflow_runs` (cascade delete).
- `(parent_step_attempt_namespace_id, parent_step_attempt_id)` on `workflow_runs` (set null).
- `(child_workflow_run_namespace_id, child_workflow_run_id)` → `workflow_runs` (set null).

#### `workflow_signals`

Stores durable signal deliveries to waiting steps.

| Column | Type | Notes |
| --- | --- | --- |
| `namespace_id` | `TEXT NOT NULL` | Part of primary key with `id`. |
| `id` | `TEXT NOT NULL` | `gen_random_uuid()` generated. |
| `signal` | `TEXT NOT NULL` | Signal name/address. |
| `data` | `JSONB` | Signal payload. |
| `sender_idempotency_key` | `TEXT` | Optional sender idempotency. |
| `workflow_run_id` | `TEXT NOT NULL` | Target run. |
| `step_attempt_id` | `TEXT NOT NULL` | Target waiting step attempt. |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` | Delivery time. |

Unique index on `(namespace_id, step_attempt_id)`.

### Indexes

- `workflow_runs_status_available_at_created_at_idx` — polling.
- `workflow_runs_workflow_name_idempotency_key_created_at_idx` — idempotency lookup.
- `workflow_runs_parent_step_idx` — child workflow lookups.
- `workflow_runs_created_at_desc_idx` / `workflow_runs_status_created_at_desc_idx` / `workflow_runs_workflow_name_status_created_at_desc_idx` — listing.
- `step_attempts_workflow_run_created_at_idx` / `step_attempts_workflow_run_step_name_created_at_idx` — step history.
- `step_attempts_child_workflow_run_idx` — child run lookups.
- `workflow_signals_step_attempt_idx` / `workflow_signals_idempotency_idx` / `step_attempts_signal_wait_idx` — signal delivery.

### Migration strategy

- A single `openworkflow_migrations` table tracks applied migration versions.
- Migrations are idempotent `CREATE IF NOT EXISTS` / `ADD IF NOT EXISTS` SQL
  blocks wrapped in transactions.
- Foreign keys are added `NOT VALID` then validated in a later migration to
  avoid locking large tables.
- `BackendPostgres.connect()` optionally runs migrations using a single
  connection pool.

### Worker model

- Stateless workers poll the database with `claimWorkflowRun(workerId, leaseDurationMs)`.
- `claimWorkflowRun` does three things atomically:
  1. Fails any runs whose `deadline_at` has passed.
  2. Selects the oldest available `pending`/`running`/`sleeping` run with
     `available_at <= NOW()` using `FOR UPDATE SKIP LOCKED`.
  3. Marks it `running`, increments `attempts`, sets `worker_id`, and sets
     `available_at` to `NOW() + lease_duration`.
- Workers heartbeat via `extendWorkflowRunLease` while executing.
- Completed/failed/canceled runs set `worker_id` to `NULL`.
- Sleep steps set `worker_id = NULL` and `available_at` to the resume time.

### Query patterns

- Run lifecycle: `createWorkflowRun`, `getWorkflowRun`, `claimWorkflowRun`,
  `extendWorkflowRunLease`, `completeWorkflowRun`, `failWorkflowRun`,
  `cancelWorkflowRun`, `rescheduleWorkflowRunAfterFailedStepAttempt`.
- Step lifecycle: `createStepAttempt`, `completeStepAttempt`,
  `failStepAttempt`, `setStepAttemptChildWorkflowRun`.
- Listing/counting: `listWorkflowRuns` (cursor pagination by `created_at, id`),
  `countWorkflowRuns` (group by status).
- Signals: `sendSignal` (matches running `signal-wait` steps and writes a
  delivery row), `getSignalDelivery`.

## Decision

Adopt the upstream schema and lifecycle semantics with ADK-specific naming:

1. Use the same three-table schema (`workflow_runs`, `step_attempts`,
   `workflow_signals`) under a configurable schema/namespace.
2. Use `asyncpg` for the implementation, matching the existing dependency.
3. Implement a `PostgresRunHistory` (or `PostgresBackend`) class with the same
   core operations as the upstream `Backend` interface.
4. Keep `InMemoryRunHistory` and `SQLiteRunHistory` for dev/tests.
5. Add database-polling worker mode as an alternative to the broker-driven
   `WorkflowWorker`.
6. Support heartbeats/lease extension and crash recovery by re-claiming runs
   whose `available_at` lease has expired.
7. Store workflow state/event logs in JSONB columns; keep normalized columns
   for query-heavy fields (`status`, `workflow_name`, `available_at`,
   `created_at`).

## Consequences

- The PostgreSQL backend gives durability, multi-worker coordination, and
  stats parity with upstream OpenWorkflow.
- Existing in-memory and SQLite backends continue to work for local
  development.
- The migration strategy is safe for production: idempotent DDL, deferred FK
  validation, and explicit migration tracking.
- A future worker implementation can poll PostgreSQL directly, removing the
  need for a separate broker in single-database deployments.
