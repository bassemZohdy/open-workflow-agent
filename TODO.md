# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file is the concise active execution backlog; completed work is recorded in project status and git history.

## Current Phase

**B-002 — Lifecycle and operational semantics.**

## Active Backlog — Ordered

### B-002 — Lifecycle and operational semantics (P2)

Goal: define portable lifecycle behavior without leaking ADK/LangGraph checkpoint or runtime representations.

- Specify a stable common event vocabulary and structured payload for invocation/task start, progress, wait, retry, fault, completion, cancellation, and resume; map both engines to it.
- Define cancellation and waiting-state transitions across restart/resume boundaries, including ownership and retry behavior for idempotent side effects.
- Add an operator-facing matrix covering fault, retry, timeout, cancellation, wait, restart/resume, and duplicate-operation handling on both engines.

Acceptance: shared lifecycle fixtures produce equivalent observable events and terminal results on both engines; cancellation, wait, resume, and duplicate-operation behavior is explicit and tested; no engine-specific checkpoint data appears in the common contract or API.

### B-003 — Deferred workflow lifecycle features (P3)

- Evaluate `listen`, `emit`, lifecycle CloudEvents, scheduling, sub-workflows, external catalogs, HITL, A2A exposure, streaming, and additional engines only after B-002 acceptance.
- Keep custom MCP/A2A protocols, visual designers, BPMN, arbitrary shell execution, and distributed scheduling out of scope unless the Project Definition changes.

## Working Rules

- Add or update tests before marking a backlog item complete.
- Keep the common core framework-neutral; adapters remain engine-owned.
- Preserve separate knowledge, memory, session, checkpoint, and invocation metadata lifecycles.
- Do not require paid model/API access or install dependencies at container startup.
