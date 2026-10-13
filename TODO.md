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
- [x] **S2-03** (L) — Prove native checkpoint recovery and reconcile native state with common invocation/side-effect records.
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
  record is accepted at that source in actual SDK probes. All seven `33cd41f`
  jobs subsequently pass, including twenty configured event/child cases per
  adapter and both PostgreSQL/main CI images. Source `e39a7c0` binds terminal
  child records/input to prepared requests and admits an action identity index
  atomically with intents. Pre-admission checks reconcile associated receipts
  and descendant actions even before native task progress saves a receipt link.
  Missing/changed child records, input, execution references, descendant receipts
  and index metadata now reject before parent mutation or dispatch. Focus passes
  77/74 per principal SDK; storage/selection checks pass 52/9. Full frozen
  core passes 1,664/621/three tracked gaps with 91.67% coverage;
  ADK 918/494, LangGraph 960/467, optional Agent Framework 1,208/703, each with three gaps.
  At `29c681d`, PR CI and five other configured runs pass, including all
  seventeen PostgreSQL child-dependency cases per adapter and atomic rollback.
  Push CI is cancelled at its Windows time limit after two process-stop failures;
  it is not an all-seven-success artifact. The worker now requires a committed
  terminal marker, uses a five-second test lease and retains production settings;
  the Windows job is bounded at twenty minutes. Process-stop focus passes 8/24
  per SDK. Final acceptance at `ddd194e` passes all seven configured runs,
  both Windows suites, both main and PostgreSQL runtime images and both native
  benchmarks. All ten scoped [recovery requirements](docs/s2-03-recovery-acceptance.md)
  are verified; later-stage features retain their owners.
  See [acceptance evidence](PROJECT.md#s2-03-checkpoint-recovery--2026-10-12-verified).
- [x] **S2-04** (M) — Complete retry predicates/backoff/jitter, task/workflow timeouts, deadlines and exception handling.
  Error-variable/status binding and common provider error replay are corrected
  at `23b9d35`; native/portable/recovery focus passes 95/29 per SDK, and the
  final error fixture passes 19/15 per environment. All seven `b6aef8c` gates
  qualify that block, including five PostgreSQL provider cases per SDK.
  Runtime timing at `f2b5c02` passes 85/39 timing/recovery and 93/26 lifecycle/
  fork checks per SDK. Jitter and duration fields now operate during a run.
  All seven `cbb06b2` gates qualify runtime timing, with eleven PostgreSQL
  timing cases per SDK. Original workflow/task deadline anchors at `17d4197`
  pass 49/61 focus and 124/167 broader recovery checks per SDK; eight actual
  deadline cases cover expiry/remaining time and missing/changed records.
  `fdd771e` fails four fixture gates and is not all-seven success. Source
  `a0ee9c0` completes saved retry decisions, standard/scoped errors and fixture
  corrections. Actual process stops pass 3/9 per SDK; full frozen core passes
  1,732/728/three gaps at 91.61%, ADK 985/579, LangGraph 1,027/552 and optional
  Agent Framework 1,277/820, each with three gaps. Current source builds/types
  pass. Fresh configured acceptance and the final
  [finite audit](docs/s2-04-retry-timeout-acceptance.md) remain open. A further
  Windows descriptor success-path fixture timeout at `3fb1041` is corrected
  by test-only `b7d8674`; catalog checks pass 41 per principal/root and the
  complete local Windows root check passes 460/12. Fresh qualification remains
  required. Final `3fb1041` has five successful runs/two Windows failures.
  Native deadline follow-up `29eeb3b` explicitly asserts the original remaining
  budget (20/0 seconds); native ADK catalog/deadline passes 49/24 and LangGraph
  deadlines 8/24. All production bytes match the verified base. See
  [verified acceptance](PROJECT.md#s2-04-retry-timeout-and-exception-work--2026-10-12-verified).
  Final exact-source `6f8e95d` passes all seven configured runs and all thirteen
  finite requirements. PostgreSQL passes 5 provider, 11 timing, 8 deadline,
  5 retry-schedule and 3 actual process-stop cases per principal, plus atomic
  policy rollback; both Windows stages, main/PG image restarts, native benchmarks
  and all six zero-advisory reports pass. Earlier failures remain historical.
  S2-05 is next; 13 of 37 backlog tasks are verified.
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
  User-requested cleanup is verified at `3f568e5`: PR/main trigger separation, independent
  Windows/native/image checks, one root contract execution, optional Framework
  coverage for common changes, and stale-PR cancellation. Required release
  gates are preserved and all six configured workflows pass. Remaining S4-07
  release/rollback and broader capability/version acceptance stay open.
- [ ] **S4-08** (M) — Decide and document old openworkflow_adk import/CLI/package compatibility; port useful developer tooling.
- [ ] **S4-09** (L) — Preserve ENGINE-5 through ENGINE-8 as optional Agent Framework hardening; require its own native acceptance.
- [ ] **S4-10** (L) — Preserve EVENT-1 through EVENT-5; add optional external brokers, AsyncAPI and A2A push only with delivery/security contracts.
- [ ] **S4-11** (L) — Keep multi-tenancy and RFC 8693/consent as tracked conditional work; do not invent a security protocol.

## Completion rule

Close a task only when its mapped features have tested outcomes, failure/restart behavior where applicable, and recorded evidence for each supported backend. Unsupported or conditional features stay visible. Existing CTK profile results are not full OWS conformance. Do not label copied source or proposed tests as implemented capabilities.

Earlier completed release/readiness work and deferred details are preserved in [the previous backlog](docs/backlog-before-consolidation-2026-10-11.md). Optional Microsoft Agent Framework hardening, external event transports, push notifications, multi-tenancy and delegated identity remain conditional; tracking them does not activate them automatically.

## Deferred tasks

No active backlog tasks are currently deferred. S2-08 is verified; S2-09 is
the next ready task. Scoped acceptance and prior failures are recorded above.
Move a task's
entry here when the blocked-task rule applies, keeping its checkbox unchecked
and including the full deferral record described above.
