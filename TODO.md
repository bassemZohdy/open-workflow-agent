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

## Delivery status — 2026-10-14

**17 verified / 20 open / 0 deferred.** S2-09 remains the earliest unfinished
task. The focused correction batch passed 377 tests (176 skipped), static and
lock checks, Compose validation, Markdown links, and root/core builds; merged
coverage is 90.0033% with the 90% gate unchanged. Hosted PR #65 then found
actionable PostgreSQL protected-approval timestamp precision failures, a
PostgreSQL sandbox-test fixture leak, two timing-sensitive Windows tests, and
the Linux process-recovery interpreter's missing pidfd API. Root CI also found
a descendant-process test setup failure and 89.54% coverage against the
unchanged 90% gate. Focused corrections and behavioral coverage additions are
assembled; the first combined local regression run passed 323 tests with 153
environmental skips. Full root and hosted qualification remain outstanding.
No task or feature advances until fresh exact-source validation passes; no task
is deferred. The Windows full-root run stopped at 33% after 45 minutes and is
incomplete. See the [current project
status](PROJECT.md#current-backlog-delivery-mode--2026-10-14) and [retained
validation evidence](docs/validation-history/pending-batch-results.md#hosted-validation-of-corrective-batch--pr-65).

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
- [x] **S2-03** (L) — Prove native checkpoint recovery and reconcile native state with common invocation/side-effect records.
  Accepted finite profile; [retained revisions, validations and exclusions](docs/validation-history/backlog-task-notes.md#s2-03).
- [x] **S2-04** (M) — Complete retry predicates/backoff/jitter, task/workflow timeouts, deadlines and exception handling.
  Accepted finite profile; [retained revisions, validations and exclusions](docs/validation-history/backlog-task-notes.md#s2-04).
- [x] **S2-05** (L) — Add an embedded durable event inbox/outbox, correlation, duplicate/late-event handling and restart recovery.
  Verified: common storage and matching, native consumption/iterator
  recovery, scope and approval guards are implemented. Focused native and
  process-stop, full-suite and configured acceptance qualify runtime source
  `143d38a` and publication `58de90e`. All fourteen finite requirements pass;
  embedded capability reports are verified. See
  [acceptance evidence](PROJECT.md#s2-05-embedded-event-work--2026-10-12-verified).
- [x] **S2-06** (L) — Add durable timers and missing schedule/event-trigger semantics; avoid keeping waits in sleeping tasks.
  Verified at `3f568e5`: common timer decisions, native nonresident suspension,
  fenced schedule slots, bounded calendars and atomic embedded event-created
  invocations. All six configured workflows, actual PostgreSQL, four runtime
  image capability observations, Windows, native CTK/benchmarks and audits pass.
  See the [finite acceptance record](docs/s2-06-timer-schedule-acceptance.md).
  Child lifecycle, external transports, full proposed/JavaScript and broader
  deployment scaling remain separately gated.
- [x] **S2-07** (L) — Complete recursive, nested and asynchronous child workflows with budgets, identity and cancellation.
  Verified finite CH01-CH10 profile at `6b23e5d`: immutable root quotas and
  pinned child identities/input, finite recursion, detached independent lifetime,
  parent-owned nonresident waits and private native fork reentry, original
  deadlines and synchronous cancellation. Actual process stop, corruption,
  context/version isolation, competing workers and stale common/native fences
  pass on both principal SDKs and PostgreSQL. All six configured workflows,
  four images, seven audits, CTK provenance and two native benchmarks pass.
  Earlier failures remain historical. See the
  [verified acceptance](docs/s2-07-child-workflow-acceptance.md).
  General lifecycle/uncertain external actions and compatible migration retain
  S2-08/S2-09 ownership; full proposed/JavaScript gates remain Stage 4.
- [x] **S2-08** (L) — Complete cancellation, termination, compensation and idempotent external-action recovery.
  Verified finite LC01-LC08 Python profile at `96ce610`: atomic authorized
  cancellation and lease revocation, host drain/ownership-loss separation,
  explicit child-scope controls, distinct logical operation keys with retained
  retry/legacy identities, opt-in trusted HTTP outcome lookup without business
  replay, and explicit compensation with visible failure/uncertainty. Both
  principal SDKs pass all 46 new cases on SQLite and actual PostgreSQL;
  process-stop, corruption, Windows, four images, seven audits, CTK provenance
  and two native benchmarks pass. Earlier failures remain historical. See the
  [verified lifecycle acceptance](docs/s2-08-lifecycle-acceptance.md).
  No automatic remote rollback or general exactly-once guarantee is claimed;
  migration, full proposed criteria and JavaScript retain S2-09/Stage 4 ownership.
- [ ] **S2-09** (L) — Define compatible workflow-version migration, crash/replay behavior and recovery diagnostics.
  Original-definition admission, exact profile checks and authenticated bounded
  recovery diagnostics are authored in an isolated batch. See the
  [finite policy](docs/s2-09-version-recovery-acceptance.md). Actual native,
  backend, process-stop, packaging and hosted qualification remain required.

## Stage 3 — Agents, tools and integrations

- [ ] **S3-01** (M) — Complete MCP tools/resources/transports/authentication with pinned official SDKs and integration tests.
  Common HTTP/SDK, bounded authentication and restricted stdio changes are
  authored; integrated qualification and publication remain pending. See the
  [finite acceptance gates](docs/s3-01-mcp-acceptance.md). No new transport,
  authentication or inventory profile is qualified before integrated validation.
- [ ] **S3-02** (L) — Complete workflow-to-agent and agent-to-workflow A2A invocation, exposure, lifecycle and interoperability.
  Typed common SDK transport and protected inbound delivery are authored;
  peer/native/backend acceptance requires fresh integrated qualification. See the
  [finite acceptance gates](docs/s3-02-a2a-acceptance.md). S2-09/S3-01 publication
  and integrated SDK/backend/platform validation remain prerequisites; no new
  A2A profile or proposed inventory criterion is complete.
- [ ] **S3-03** (L) — Complete HTTP/OpenAPI/gRPC/AsyncAPI, function and background-operation adapters as required by the inventory.
  Selected HTTP/OpenAPI/gRPC, awaited outputs, pure AsyncAPI descriptions and
  independent jobs are authored; corrections and qualification remain active. See
  [the finite acceptance plan](docs/s3-03-operation-adapters-acceptance.md).
  Behavioral/native/backend/platform, physical lifetime, packaging and hosted
  qualification remain outstanding; no new profile or inventory row is complete.
- [ ] **S3-04** (M) — Complete executable-operation portability through the existing SandboxManager and proven isolation profiles.
  Finite captured script sources, independent recovery proofs and awaited internal
  process controls are authored. See [the acceptance plan](docs/s3-04-executable-source-acceptance.md).
  The coherent source batch and all platform/backend/publication gates remain
  pending; no new executable or isolation profile is complete.
- [ ] **S3-05** (M) — Reuse durable protected approvals; add reassignment, escalation, waiting, denial/expiry and resume tests.
  Protected actor identity, atomic assignments/decisions, fair expiry and native
  recovery proofs are authored; see [the finite acceptance plan](docs/s3-05-protected-approval-acceptance.md).
  No behavioral or publication gate is complete.
- [ ] **S3-06** (L) — Complete identity propagation, credential references, policy/audit/isolation and applicable delegated authorization.
- [ ] **S3-07** (M) — Finish all eight legacy agent patterns as bounded OWS workflows or explicit native extensions.
  Eight finite recipe families and named role bindings are authored; see
  [the acceptance plan](docs/s3-07-agent-pattern-acceptance.md). Required validation remains open.
- [ ] **S3-08** (L) — Port selected ADK compiler/provider/broker/memory patterns and fixtures against canonical contracts.
  Selected finite adapters and grouped corrections are authored; see
  [the acceptance plan](docs/s3-08-selected-adapter-acceptance.md). Qualification remains pending.

## Stage 4 — Advanced features and full verification

- [ ] **S4-01** (M) — Implement versioned native YAML metadata extensions with capability admission; reject unsupported requests.
- [ ] **S4-02** (L) — Add declared native channels/reducers, Command/Send, memory, streams, state updates, replay and time travel.
- [ ] **S4-03** (L) — Implement and independently qualify JavaScript/TypeScript; keep Python-only and JS-only differences visible.
- [ ] **S4-04** (L) — Automate official CTK plus all proposed tests, schema constraints, parity and cross-engine outcome checks.
- [ ] **S4-05** (M) — Complete execution history, tracing, lifecycle metrics, correlation, provenance and replay diagnostics.
- [ ] **S4-06** (M) — Run matched latency/CPU/memory/concurrency/persistence/recovery benchmarks; publish measured results.
- [ ] **S4-07** (M) — Keep CI, builds, releases, capability reports and rollback tied to verified versions and acceptance gates.
  User-requested cleanup is verified at `3f568e5`: PR/main trigger separation, independent
  Windows/native/image checks, one root contract execution, optional Framework
  coverage for common changes, and stale-PR cancellation. Required release
  gates were preserved and all six configured workflows passed at that checkpoint.
  The later hosted checkpoint failed, as recorded above. Remaining S4-07
  release/rollback and broader capability/version acceptance stay open.
- [ ] **S4-08** (M) — Decide and document old openworkflow_adk import/CLI/package compatibility; port useful developer tooling.
- [ ] **S4-09** (L) — Preserve ENGINE-5 through ENGINE-8 as optional Agent Framework hardening; require its own native acceptance.
- [ ] **S4-10** (L) — Preserve EVENT-1 through EVENT-5; add optional external brokers, AsyncAPI and A2A push only with delivery/security contracts.
- [ ] **S4-11** (L) — Keep multi-tenancy and RFC 8693/consent as tracked conditional work; do not invent a security protocol.

## Completion rule

Close a task only when its mapped features have tested outcomes, failure/restart behavior where applicable, and recorded evidence for each supported backend. Unsupported or conditional features stay visible. Existing CTK profile results are not full OWS conformance. Do not label copied source or proposed tests as implemented capabilities.

Earlier completed release/readiness work and deferred details are preserved in [the previous backlog](docs/backlog-before-consolidation-2026-10-11.md). Optional Microsoft Agent Framework hardening, external event transports, push notifications, multi-tenancy and delegated identity remain conditional; tracking them does not activate them automatically.

## Deferred tasks

No active backlog task is currently deferred. Actionable validation failures
remain with their original task. Move an entry here only when the blocked-task
rule applies, preserving its unchecked scope and the full deferral record.
