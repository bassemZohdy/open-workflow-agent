# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file is the concise active execution backlog; completed work is recorded in project status and git history.

## Current Phase

**B-003 — Deferred workflow lifecycle features.** Bounded eventing, lifecycle CloudEvents, scheduling, and local sub-workflows are complete.

## Active Backlog — Ordered

### B-003 — Deferred workflow lifecycle features (P3)

Prioritized next backlog, ordered by dependency and the Project Definition:

1. **P3 — durable HITL and external catalogs:** the contract boundary is now specified in `Project Definition.md`; implement only after durable approval state, operator authorization, idempotency, restart/replay, and secure catalog resolution have deterministic cross-engine contracts. Keep external catalogs disabled until then.
2. **P3 — A2A exposure and streaming:** evaluate only as optional capabilities with explicit engine support and no portability claim by default.
3. **P3 — additional engines:** add another adapter only after the shared contracts remain framework-neutral and the existing cross-engine fixtures are extended.

The completed eventing slice supports `emit`, `listen` with the `one` strategy, and
`POST /v1/events` through a process-local non-durable bus. `all`, `any`, `foreach`,
replay, and durable broker delivery remain explicitly unsupported.

The completed lifecycle CloudEvents slice exposes common lifecycle events through
`GET /v1/events/lifecycle` as bounded CloudEvents 1.0 JSON batches. It is a
non-streaming, non-durable snapshot and does not expose engine-native state.

The completed scheduling slice supports durable `schedule.after` one-shot and
`schedule.every` recurring workflow starts through `POST /v1/schedules`. A single
runtime process owns dispatch, leases due rows for restart reclaim, and preserves
at-least-once execution. `cron`, event-triggered `on`, distributed ownership,
and scheduler streaming remain explicitly unsupported.

The completed sub-workflow slice supports the Open Workflow `run` task against
explicitly configured local workflow definitions (`workflow.catalog`). Child
invocations receive separate common invocation/session metadata and engine-owned
execution state, with parent invocation/task references retained in common
lifecycle events. Shell/script execution and external/remote catalogs remain
explicitly unsupported.

The next HITL/catalog contract is deliberately bounded: generic `listen` may
consume an operator-provided event, but the current runtime has no durable
approval inbox, approval identity/authorization, replay guarantee, or dedicated
approval API. Open Workflow `use.catalogs` definitions are rejected explicitly;
only deployment-provided local workflow definitions are supported.

Custom MCP/A2A protocols, visual designers, BPMN, arbitrary shell execution, and distributed scheduling remain out of scope unless the Project Definition changes.

## Working Rules

- Add or update tests before marking a backlog item complete.
- Keep the common core framework-neutral; adapters remain engine-owned.
- Preserve separate knowledge, memory, session, checkpoint, and invocation metadata lifecycles.
- Do not require paid model/API access or install dependencies at container startup.
