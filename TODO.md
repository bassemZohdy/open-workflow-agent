# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file is the concise active execution backlog; completed work is recorded in project status and git history.

## Current Phase

**B-003 — Deferred workflow lifecycle features.** Bounded eventing and the optional lifecycle CloudEvents boundary are complete.

## Active Backlog — Ordered

### B-003 — Deferred workflow lifecycle features (P3)

Prioritized next backlog, ordered by dependency and the Project Definition:

1. **P2 — scheduling:** define bounded, durable scheduling semantics only after event delivery and operational ownership are explicit.
2. **P2 — sub-workflows:** add workflow composition and child invocation identity while preserving separate invocation, session, checkpoint, memory, and knowledge lifecycles.
3. **P3 — HITL and external catalogs:** specify security, persistence, approval, and capability contracts before implementation; keep external catalogs disabled by default.
4. **P3 — A2A exposure and streaming:** evaluate only as optional capabilities with explicit engine support and no portability claim by default.
5. **P3 — additional engines:** add another adapter only after the shared contracts remain framework-neutral and the existing cross-engine fixtures are extended.

The completed eventing slice supports `emit`, `listen` with the `one` strategy, and
`POST /v1/events` through a process-local non-durable bus. `all`, `any`, `foreach`,
replay, and durable broker delivery remain explicitly unsupported.

The completed lifecycle CloudEvents slice exposes common lifecycle events through
`GET /v1/events/lifecycle` as bounded CloudEvents 1.0 JSON batches. It is a
non-streaming, non-durable snapshot and does not expose engine-native state.

Custom MCP/A2A protocols, visual designers, BPMN, arbitrary shell execution, and distributed scheduling remain out of scope unless the Project Definition changes.

## Working Rules

- Add or update tests before marking a backlog item complete.
- Keep the common core framework-neutral; adapters remain engine-owned.
- Preserve separate knowledge, memory, session, checkpoint, and invocation metadata lifecycles.
- Do not require paid model/API access or install dependencies at container startup.
