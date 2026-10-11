# Open Workflow Agent — implementation gaps

`open-workflow-agent` is the main repository. Implement these stages in order, with the blocked-task exception below; keep every gap tracked from day one. Existing shipped behavior is recorded in [PROJECT.md](PROJECT.md), not assumed missing or conformant here.

If repeated attempts leave a task stuck with no actionable next step, move its
original unchecked entry to **Deferred tasks** at the end of this backlog and
continue the next ready task. Preserve its ID, full scope and verified progress.
Each deferral must describe the blocker and evidence, unsuccessful attempts,
remaining implementation and required validations, dependency impact, and the
condition and next action for resuming it. Keep `PROJECT.md` and the feature
tracker consistent. Deferral does not imply completion or relax dependency,
security, portability or capability acceptance gates. Running validations and
actionable failures remain active work.

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
- [x] **S1-03** (M) — Fix input/output/export ordering, context replacement, task-local data and immutable branch input.
  Verified at `56e9f2c`: [data pipeline](docs/workflow-data-flow.md) and
  [acceptance evidence](PROJECT.md#s1-03-data-flow-work--2026-10-11-verified).
- [x] **S1-04** (M) — Preserve transitions, loop/condition rules, and the different scopes of exit and end.
  Verified at `0dc871d`: [control flow](docs/workflow-control-flow.md) and
  [acceptance evidence](PROJECT.md#s1-04-control-flow-work--2026-10-11-verified).
- [x] **S1-05** (L) — Compile individual tasks and routing to native LangGraph nodes/tasks; keep native checkpoints private.
  Verified at `6a3afd7`: [native task execution](docs/native-task-execution.md) and
  [acceptance evidence](PROJECT.md#s1-05-native-task-execution--2026-10-11-verified).
- [x] **S1-06** (M) — Provide one-container embedded defaults; separate workflow behavior from external bootstrap/provider settings.
  Verified at `c2094a0`: [embedded bootstrap defaults](docs/embedded-bootstrap.md) and
  [acceptance evidence](PROJECT.md#s1-06-embeddedbootstrap-defaults--2026-10-11-verified).
- [x] **S1-07** (M) — Complete reusable definitions, imports, references, validation, version resolution and safe serialization.
  Verified at `f7be5d1`: [reusable resolution](docs/reusable-definitions.md),
  [schema resources](docs/schema-resources.md) and
  [acceptance evidence](PROJECT.md#s1-07-reusable-definitions-and-resource-identity--2026-10-11-verified).
- [x] **S1-08** (M) — Verify the current implementation against every tracked feature before claiming it complete.
  Verified at `a1cbae8`: [all 658 implementation outcomes](docs/current-implementation-audit.md)
  and [acceptance evidence](PROJECT.md#s1-08-current-implementation-audit--2026-10-11-verified).
  Audit completion does not close the remaining feature gaps or proposed full acceptance tests.
- [x] **MIG-02** (S) — Archive the three preserved predecessors after consolidation is merged.
  All three archived states confirmed against GitHub metadata; source preservation checks pass.
  See [archive and rollback](docs/agent-runtime-consolidation.md#archive-and-rollback).

## Stage 2 — Reliable execution and recovery

- [x] **S2-01** (M) — Preserve ordered fork results, branch isolation, reducers and state-collision behavior.
  Runtime verified at `6c84072`, with additional cross-adapter restart fixtures at
  `ad33b4f`: [fork behavior](docs/workflow-forks.md) and
  [acceptance evidence](PROJECT.md#s2-01-ordered-fork-results--2026-10-11-verified).
  Public native channel/reducer extensions remain S4-01/S4-02; full proposed
  criteria and independent JS qualification remain S4-03/S4-04.
- [x] **S2-02** (L) — Implement first-completed competitive forks, durable winner selection and loser cancellation.
  Runtime verified at `2fbe548`, repeated-scope recovery at `51c204e`, and all three
  common PostgreSQL bindings at `81d27b3`: [fork behavior](docs/workflow-forks.md) and
  [acceptance evidence](PROJECT.md#s2-02-competitive-fork-decisions--2026-10-11-verified).
  General pending effects/compensation and full native/JS/proposed criteria retain later-stage owners.
- [ ] **S2-03** (L) — Prove native checkpoint recovery and reconcile native state with common invocation/side-effect records.
  JSON resume-value preservation is verified at `f2943c6`; original-input and
  terminal-result recovery at `6840605`, and journal storage at `d197fa1`.
  Leaf-call action receipts and corrected dependency validation are verified at
  `fc53574`; conditional invocation writes passed all gates at `6954db8`.
  Initial native/input-binding admission passed all gates at `b9a2b31`;
  eight forced process-stop cases per SDK/common bridge passed SQLite/PostgreSQL
  acceptance at `ef29f23`. Private expiring ownership storage at `75fbc0d`
  passed all gates at `cdd6e17`. Runtime admission and atomic common/native
  checkpoint fencing at `b2dfc5c` pass local, Windows, native and PostgreSQL
  suites; both main CI image restart gates fail immediate resume while the
  stopped owner's lease is live. Expiry-aware image acceptance and action/fork
  generation guards passed all seven gates at `2acf632`, including both actual
  forced-stop runtime images. Root/nested LangGraph corruption checks at
  `556da47` pass local native/shared/CTK, Windows and image regression at
  `fc54600`; its PostgreSQL corruption fixture fails before changing SDK state.
  The corrected fixture and ADK all-input-event validation at `005ab5b` pass
  local checks and full ADK regression; all seven gates pass at `4058895`, with
  127 configured LangGraph and 108 ADK recovery cases. Boolean terminal controls
  contradicting native routing are rejected at `e61c6a5`; full local LangGraph
  regression passes; `6af95cf` routing/PostgreSQL/push image gates pass, while
  PR CI fails its ADK storage benchmark. Source `9755e96` binds native progress
  to common receipts and drains ADK SQLite reads through the existing worker.
  Shared root/nested value/null/fault and waiting-fork receipt checks pass on both
  principal SDKs. Full frozen core/LangGraph/optional regression passes; final
  ADK native/shared/CTK passes 808/366/three tracked gaps and its benchmark passes.
  Missing-receipt assertions fail on prior source on both SDKs. All seven
  `c276f40` gates pass, including both main CI force-stop images and PostgreSQL
  159 LangGraph/134 ADK recovery cases. Source `968ec8f` binds completed scope
  checkpoints to later native progress. Deleted or consistently changed child
  checkpoints reject recovery; a cancelled child without its first checkpoint
  remains valid. Focus passes 65/117 and full LangGraph native/shared/CTK passes
  850/357/three tracked gaps. All seven `ac478e1` jobs succeed, but their
  PostgreSQL filter deselects all nine completed-scope cases; those cases were
  unqualified at that revision. Source `3df61f6` corrects the selector and checks
  actual collection.
  Common sandbox receipts preserve known script/shell/container outcomes across
  native recovery and stop changed requests or uncertain intents before another
  dispatch. Frozen full core passes 1,583/546/three tracked gaps with 91.50%
  coverage; ADK 844/420, LangGraph 886/393 and optional Agent Framework
  1,097/592 each retain three tracked gaps. Focus passes 49/36 on each principal
  SDK. All seven `c7b70ad` jobs pass; PostgreSQL actually executes all nine scope
  cases and eighteen sandbox cases per adapter, with 186 LangGraph/152 ADK
  shared recovery passes and both runtime images. Source `5506277` additionally
  records event and synchronous child outcomes before native progress; both
  focused suites pass 70/64. Full frozen core passes 1,625/586
  with 91.58% coverage; ADK 884/460, LangGraph 926/433 and optional
  Agent Framework 1,157/652,
  each with three tracked gaps. A completed child's missing common invocation
  record is still accepted in an actual SDK probe. Child-record dependency
  reconciliation, fresh event/child acceptance and complete native/common
  consistency remain open.
  See [current progress](PROJECT.md#s2-03-checkpoint-recovery--2026-10-11-in-progress).
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

## Deferred tasks

No active backlog tasks are currently deferred. S2-03 has verified recent
progress and a reproducible child-record dependency gap with an actionable
implementation path; its fresh remote validations are running. Move a task's
entry here when the blocked-task rule applies, keeping its checkbox unchecked
and including the full deferral record described above.
