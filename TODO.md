# Open Workflow Agent — implementation gaps

`open-workflow-agent` is the main repository. Implement these stages in order; keep every gap tracked from day one. Existing shipped behavior is recorded in [PROJECT.md](PROJECT.md), not assumed missing or conformant here.

The [full tracker](resources/compatibility/feature-tracker.json) preserves **658 features, 443 proposed tests and 774 schema constraints**, with Python/JS status and links to tasks below. All new delivery claims await verification. The [migration record](docs/agent-runtime-consolidation.md) distinguishes imported reference material from active code.

Rules: reuse native LangGraph graphs/tasks/checkpoints; start in one container with embedded storage; make external services optional configuration; put task/workflow behavior in workflow YAML/JSON and environment preparation in bootstrap configuration. Versioned native features use custom metadata extensions and explicit capability checks.

Effort: **S** small, **M** medium, **L** substantial. These are relative sizes, not time estimates.

## Stage 1 — Correct core execution

- [x] **S1-01** (M) — Pin the supported OWS schema, release status and semantic profile; reject unsupported definitions clearly.
  Verified at `df47361`: [admission profile](docs/workflow-admission.md) and
  [acceptance evidence](PROJECT.md#stage-1-admission-work--2026-10-11-verified).
- [x] **S1-02** (L) — Replace the partial expression evaluator with validated jq behavior and consistent data/type handling.
  Verified at `3ab2e73`: [jq semantics and limits](docs/workflow-expressions.md) and
  [acceptance evidence](PROJECT.md#s1-02-expression-work--2026-10-11-verified).
- [ ] **S1-03** (M) — Fix input/output/export ordering, context replacement, task-local data and immutable branch input.
- [ ] **S1-04** (M) — Preserve transitions, loop/condition rules, and the different scopes of exit and end.
- [ ] **S1-05** (L) — Compile individual tasks and routing to native LangGraph nodes/tasks; keep native checkpoints private.
- [ ] **S1-06** (M) — Provide one-container embedded defaults; separate workflow behavior from external bootstrap/provider settings.
- [ ] **S1-07** (M) — Complete reusable definitions, imports, references, validation, version resolution and safe serialization.
- [ ] **S1-08** (M) — Verify the current implementation against every tracked feature before claiming it complete.
- [ ] **MIG-02** (S) — Archive the three preserved predecessors after consolidation is merged; currently requires user action.

## Stage 2 — Reliable execution and recovery

- [ ] **S2-01** (M) — Preserve ordered fork results, branch isolation, reducers and state-collision behavior.
- [ ] **S2-02** (L) — Implement first-completed competitive forks, durable winner selection and loser cancellation.
- [ ] **S2-03** (L) — Prove native checkpoint recovery and reconcile native state with common invocation/side-effect records.
- [ ] **S2-04** (M) — Complete retry predicates/backoff/jitter, task/workflow timeouts, deadlines and exception handling.
- [ ] **S2-05** (L) — Add an embedded durable event inbox/outbox, correlation, duplicate/late-event handling and restart recovery.
- [ ] **S2-06** (L) — Add durable timers and missing schedule/event-trigger semantics; avoid keeping waits in sleeping tasks.
- [ ] **S2-07** (L) — Complete recursive, nested and asynchronous child workflows with budgets, identity and cancellation.
- [ ] **S2-08** (L) — Complete cancellation, termination, compensation and idempotent external-action recovery.
- [ ] **S2-09** (L) — Define compatible workflow-version migration, crash/replay behavior and recovery diagnostics.

## Stage 3 — Agents, tools and integrations

- [ ] **S3-01** (M) — Complete MCP tools/resources/transports/authentication with pinned official SDKs and integration tests.
- [ ] **S3-02** (L) — Complete workflow-to-agent and agent-to-workflow A2A invocation, exposure, lifecycle and interoperability.
- [ ] **S3-03** (L) — Complete HTTP/OpenAPI/gRPC/AsyncAPI, function and background-operation adapters as required by the inventory.
- [ ] **S3-04** (M) — Complete executable-operation portability through the existing SandboxManager and proven isolation profiles.
- [ ] **S3-05** (M) — Reuse durable protected approvals; add reassignment, escalation, waiting, denial/expiry and resume tests.
- [ ] **S3-06** (L) — Complete identity propagation, credential references, policy/audit/isolation and applicable delegated authorization.
- [ ] **S3-07** (M) — Finish all eight legacy agent patterns as bounded OWS workflows or explicit native extensions.
- [ ] **S3-08** (L) — Port selected ADK compiler/provider/broker/memory patterns and fixtures against canonical contracts.

## Stage 4 — Advanced features and full verification

- [ ] **S4-01** (M) — Implement versioned native YAML metadata extensions with capability admission; reject unsupported requests.
- [ ] **S4-02** (L) — Add declared native channels/reducers, Command/Send, memory, streams, state updates, replay and time travel.
- [ ] **S4-03** (L) — Implement and independently qualify JavaScript/TypeScript; keep Python-only and JS-only differences visible.
- [ ] **S4-04** (L) — Automate official CTK plus all proposed tests, schema constraints, parity and cross-engine outcome checks.
- [ ] **S4-05** (M) — Complete execution history, tracing, lifecycle metrics, correlation, provenance and replay diagnostics.
- [ ] **S4-06** (M) — Run matched latency/CPU/memory/concurrency/persistence/recovery benchmarks; publish measured results.
- [ ] **S4-07** (M) — Keep CI, builds, releases, capability reports and rollback tied to verified versions and acceptance gates.
- [ ] **S4-08** (M) — Decide and document old openworkflow_adk import/CLI/package compatibility; port useful developer tooling.
- [ ] **S4-09** (L) — Preserve ENGINE-5 through ENGINE-8 as optional Agent Framework hardening; require its own native acceptance.
- [ ] **S4-10** (L) — Preserve EVENT-1 through EVENT-5; add optional external brokers, AsyncAPI and A2A push only with delivery/security contracts.
- [ ] **S4-11** (L) — Keep multi-tenancy and RFC 8693/consent as tracked conditional work; do not invent a security protocol.

## Completion rule

Close a task only when its mapped features have tested outcomes, failure/restart behavior where applicable, and recorded evidence for each supported backend. Unsupported or conditional features stay visible. Existing CTK profile results are not full OWS conformance. Do not label copied source or proposed tests as implemented capabilities.

Earlier completed release/readiness work and deferred details are preserved in [the previous backlog](docs/backlog-before-consolidation-2026-10-11.md). Optional Microsoft Agent Framework hardening, external event transports, push notifications, multi-tenancy and delegated identity remain conditional; tracking them does not activate them automatically.
