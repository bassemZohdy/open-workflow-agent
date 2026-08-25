# Open Workflow Agent Backlog

`Project Definition.md` is authoritative. `AGENTS.md` defines repository rules. This file is the concise execution backlog; completed work is retained as history so active tasks stay actionable.

## Current Phase

**B-002 — Lifecycle and operational semantics.**

B-001 is complete and verified. The next step is to define portable lifecycle behavior around invocation/task events, cancellation, waiting, restart/resume, and idempotent side effects without exposing engine-specific checkpoint representations.

## Completed Work

- **A-001 — Remote CI and release acceptance:** Ubuntu CI is green from a clean checkout. Root tests/contracts, exact lock checks, wheel resource validation, release/image metadata, image-size gates, Docker health/invocation/knowledge acceptance, retained logs, and real stop/restart/resume acceptance pass for both engines.
- **A-002 — CTK compatibility gate:** the selected Portable Profile subset (`do`, `set`, `switch`, `for`, `branch`, `raise`) passes in both engine jobs. The pinned upstream CTK commit, repository commit, scenario hashes, and test output are uploaded as `ctk-adk-results` and `ctk-langgraph-results` artifacts.
- **A-003 — Configured external persistence:** locked PostgreSQL support is implemented for common invocation metadata, memory, and knowledge metadata, with isolated namespaces and engine-native ADK/LangGraph persistence. Unit/integration tests and Docker restart acceptance pass for both images; unsupported datasource schemes fail explicitly.
- **B-001 — Extended Workflow Calls and conformance:** bounded HTTP, MCP, A2A, and OpenAPI workflow-call contracts are explicit; agent tools remain separate; policy semantics for `try`, retry, timeout, wait, and raise are covered; capabilities are truthful; and the pinned CTK subset was expanded with supported `branch` and `raise` scenarios. Both engines pass the shared contract and CTK coverage.
- **Local verification:** root suite `96 passed, 6 skipped`; Ruff, mypy, lock checks, engine suites, and contract/CTK checks pass. Local Docker builds reached model warmup but were blocked by the sandbox certificate chain; the remote Docker acceptance is green.

Evidence: green GitHub Actions run [`32816463784`](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/32816463784) on the B-001 implementation commit. It passed root quality/build gates, both engine suites, the selected CTK subset, both Docker acceptance jobs, and PostgreSQL persistence acceptance.

## Active Backlog — Ordered

### B-002 — Lifecycle and operational semantics (P2)

Goal: define portable lifecycle behavior without leaking ADK/LangGraph checkpoint or runtime representations.

- Specify a stable common event vocabulary and structured payload for invocation/task start, progress, wait, retry, fault, completion, cancellation, and resume; map both engines to it.
- Define cancellation and waiting-state transitions across restart/resume boundaries, including ownership and retry behavior for idempotent side effects.
- Add an operator-facing matrix covering fault, retry, timeout, cancellation, wait, restart/resume, and duplicate-operation handling on both engines.

Acceptance: shared lifecycle fixtures produce equivalent observable events and terminal results on both engines; cancellation, wait, resume, and duplicate-operation behavior is explicit and tested; no engine-specific checkpoint data appears in the common contract or API.

### B-003 — Deferred workflow lifecycle features (P3)

- Evaluate `listen`, `emit`, lifecycle CloudEvents, scheduling, sub-workflows, external catalogs, HITL, A2A exposure, streaming, and additional engines only after B-001/B-002 acceptance.
- Keep custom MCP/A2A protocols, visual designers, BPMN, arbitrary shell execution, and distributed scheduling out of scope unless the Project Definition changes.

## Working Rules

- Add or update tests before marking a backlog item complete.
- Keep the common core framework-neutral; adapters remain engine-owned.
- Preserve separate knowledge, memory, session, checkpoint, and invocation metadata lifecycles.
- Do not require paid model/API access or install dependencies at container startup.
