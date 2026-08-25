# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file is the concise execution backlog; completed work is retained as history so active tasks stay actionable.

## Current Phase

**Milestone 7 — Extended Workflow Calls and Portable Profile conformance.**

The next step is to make the existing bounded HTTP/MCP/A2A/OpenAPI adapters and workflow policies (`try`, `retry`, `timeout`, `wait`, and `raise`) explicit, capability-accurate, and demonstrably equivalent across ADK and LangGraph. This is a conformance and hardening milestone; it must preserve the framework-neutral core, engine-native durability, and Open Workflow 1.0.3 contract.

## Completed Work

- **A-001 — Remote CI and release acceptance:** Ubuntu CI is green from a clean checkout. Root tests/contracts, exact lock checks, wheel resource validation, release/image metadata, image-size gates, Docker health/invocation/knowledge acceptance, retained logs, and real stop/restart/resume acceptance pass for both engines.
- **A-002 — CTK compatibility gate:** the selected Portable Profile subset (`do`, `set`, `switch`, `for`) passes in both engine jobs. The pinned upstream CTK commit, repository commit, scenario hashes, and test output are uploaded as `ctk-adk-results` and `ctk-langgraph-results` artifacts.
- **A-003 — Configured external persistence:** locked PostgreSQL support is implemented for common invocation metadata, memory, and knowledge metadata, with isolated namespaces and engine-native ADK/LangGraph persistence. Unit/integration tests and Docker restart acceptance pass for both images; unsupported datasource schemes fail explicitly.
- **Local verification:** root suite `83 passed, 6 skipped`; Ruff, mypy, lock checks, wheel checks, engine suites, and local container gates pass.

Evidence: green GitHub Actions run [`32807640820`](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/32807640820) on the current `main` documentation commit.

## Active Backlog — Ordered

### B-001 — Milestone 7: Extended Workflow Calls and conformance (P1)

Goal: provide a precise, portable contract for supported workflow calls and control-flow policies without claiming protocol features that are not implemented.

- Inventory the implemented workflow-call and policy behavior against the official Open Workflow 1.0.3 schema and the declared Portable Profile.
- Add shared fixtures and contract assertions for HTTP, MCP, A2A, and OpenAPI workflow calls, including request shape, response mapping, operation identifiers/idempotency headers, timeout, redirect, response-size, authentication, and error translation.
- Keep configured agent tools separate from explicit workflow calls; test both paths on ADK and LangGraph with identical inputs and expected outputs.
- Complete and test `try`, retry limits, timeout behavior, `wait`, and `raise`, including faulted, retried, waiting, and resumed invocation states where the engine supports them.
- Reconcile `/v1/capabilities` with the actual supported task/function/protocol surface and add a contract test preventing under- or over-reporting.
- Expand the pinned CTK subset only for scenarios supported by the implemented Portable Profile; preserve upstream commit pinning, scenario hashes, and CI artifacts.
- Run local root/engine suites and the full Ubuntu workflow; retain protocol/CTK artifacts and document any intentionally unsupported scenario.

Acceptance: both engines produce equivalent results for the shared fixtures; unsupported protocol/task features fail explicitly; capabilities, docs, and CTK coverage agree; CI is green.

### B-002 — Lifecycle and operational semantics (P2)

- Map common invocation/task events to stable structured logs and metrics without exposing engine-specific checkpoint representations.
- Define cancellation and waiting-state behavior for long-running calls, including restart/resume boundaries and idempotent side effects.
- Add an operator-facing test matrix for fault, retry, timeout, cancellation, resume, and duplicate-operation handling.

### B-003 — Deferred workflow lifecycle features (P3)

- Evaluate `listen`, `emit`, lifecycle CloudEvents, scheduling, sub-workflows, external catalogs, HITL, A2A exposure, streaming, and additional engines only after B-001/B-002 acceptance.
- Keep custom MCP/A2A protocols, visual designers, BPMN, arbitrary shell execution, and distributed scheduling out of scope unless the Project Definition changes.

## Working Rules

- Add or update tests before marking a backlog item complete.
- Keep the common core framework-neutral; adapters remain engine-owned.
- Preserve separate knowledge, memory, session, checkpoint, and invocation metadata lifecycles.
- Do not require paid model/API access or install dependencies at container startup.
