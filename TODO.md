# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file is the concise active execution backlog; completed work is recorded in project status and git history.

## Current Phase

**B-003 — Deferred workflow lifecycle features.** Bounded eventing, lifecycle CloudEvents, scheduling, local sub-workflows, and bounded durable HITL approvals are complete.

## Active Backlog — Ordered

### B-003 — Deferred workflow lifecycle features (P3)

Prioritized remaining backlog, ordered by dependency and the Project Definition:

1. **P3 — secure external catalogs:** implement a deployment-controlled resolver for supported Open Workflow catalog references with explicit trust configuration, TLS verification, timeouts, bounded responses, redirect policy, host allowlists, integrity/version pinning, caching/revalidation, fail-closed behavior, and deterministic cross-engine contracts. Keep `use.catalogs` rejected until this acceptance is complete.
2. **P3 — A2A exposure and streaming:** evaluate only as optional capabilities with explicit engine support and no portability claim by default.
3. **P3 — additional engines:** add another adapter only after the shared contracts remain framework-neutral and the existing cross-engine fixtures are extended.

## Completed B-003 slices

- **Bounded eventing:** `emit`, `listen` with the `one` strategy, and `POST /v1/events` use a process-local non-durable bus. `all`, `any`, `foreach`, general replay, and durable broker delivery remain unsupported.
- **Lifecycle CloudEvents:** `GET /v1/events/lifecycle` exposes bounded CloudEvents 1.0 JSON snapshots without engine-native state; it is non-streaming and non-durable.
- **Durable scheduling:** `schedule.after` and `schedule.every` use persisted leases and restart reclaim with at-least-once execution. `cron`, event-triggered `on`, distributed ownership, and scheduler streaming remain unsupported.
- **Local sub-workflows:** the Open Workflow `run` task resolves deployment-configured local workflow definitions from `workflow.catalog`; child invocations retain separate common metadata and engine-owned execution state. Shell/script execution remains disabled.
- **Bounded durable HITL:** approval requests and decisions compose with standard `emit`/`listen` events rather than introducing a proprietary workflow task. Approval state is persisted in an isolated common store for SQLite/PostgreSQL, decisions are operator-authorized and idempotent, approval inbox reads are protected, and persisted decisions replay through the normal `listen` path after restart. Shared ADK/LangGraph contracts verify equivalent replay behavior. The current authorization boundary is a deployment-configured bearer secret plus operator identity header; it is intentionally not a full identity-management system.

External catalogs remain explicitly disabled. Generic workflow event delivery remains process-local/non-durable; durability is currently provided only for the bounded approval contract and scheduler state.

Custom MCP/A2A protocols, visual designers, BPMN, arbitrary shell execution, and distributed scheduling remain out of scope unless the Project Definition changes.

## Working Rules

- Add or update tests before marking a backlog item complete.
- Keep the common core framework-neutral; adapters remain engine-owned.
- Preserve separate knowledge, memory, session, checkpoint, invocation metadata, approval, and schedule lifecycles.
- Do not require paid model/API access or install dependencies at container startup.
- Do not advertise external catalog, A2A exposure, streaming, or additional-engine portability before deterministic contracts and capabilities prove it.
