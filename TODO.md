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

## Historical verified prerequisites — 2026-10-14

This dated snapshot records the accepted prerequisites for the active S3-02
work; [PROJECT.md](PROJECT.md) is the current source for backlog counts and
delivery status. S2-09's finite VM01-VM08 continuation/recovery policy remains
verified at PR head `9578459`. S3-01's finite Python MCP01-MCP08 profile is verified at
exact source `af4acd61c200787fb68248b2d180ba69efb8c837`; all six hosted
workflows passed. The root suite passed 4,569 tests, skipped 2,950 and reached
90.04% coverage; the Windows operation-recovery and timer suites passed 962/454
and 718/769 tests respectively. PostgreSQL passed all ten jobs. See the [complete
qualification record](docs/validation-history/pending-batch-results.md#exact-source-qualification-af4acd6)
and the [S3-01 finite acceptance gates](docs/s3-01-mcp-acceptance.md).

JavaScript, full MCP protocol coverage (including A145), proposed T29/T30/T31
criteria and broader MCP extensions remain independently unqualified. Earlier
timeouts, superseded attempts and their corrections remain in the
[validation history](docs/validation-history/pending-batch-results.md); none is
treated as a deferred task or as additional feature completion.

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
  Current-source requalification note: Windows run `38632792174` failed the
  duration-bound retry output assertion on both engines. Full local retry
  selections pass on both engines; the assertion now captures output, error,
  calls, sleeps, draws and clock for a hosted rerun. See the
  [exact-source history](docs/validation-history/pending-batch-results.md#windows-timing-suite-follow-up-at-8183776).
  The checked finite profile remains the earlier accepted scope; this is a
  later regression requalification against its corrected duration-bound witness.
- [x] **S2-05** (L) — Add an embedded durable event inbox/outbox, correlation, duplicate/late-event handling and restart recovery.
  The finite profile is verified at runtime source `143d38a` and publication
  `58de90e`; all fourteen requirements and embedded capability reports passed.
  Regression requalification at `9d4d3ec` corrected the nondurable-listener
  test to expect capability rejection before ownership, with unchanged input
  and state. A pre-ownership invoke reorder broke Windows gRPC and PostgreSQL
  approval gates and was reverted. Corrected local event/gRPC selectors pass;
  exact-source hosted requalification remains pending. See the
  [verified profile](PROJECT.md#s2-05-embedded-event-work--2026-10-12-verified)
  and [regression record](docs/validation-history/pending-batch-results.md#event-backend-admission-order-follow-up-at-9d4d3ec).
- [x] **S2-06** (L) — Add durable timers and missing schedule/event-trigger semantics; avoid keeping waits in sleeping tasks.
  Verified at `3f568e5`: common timer decisions, native nonresident suspension,
  fenced schedule slots, bounded calendars and atomic embedded event-created
  invocations. All six configured workflows, actual PostgreSQL, four runtime
  image capability observations, Windows, native CTK/benchmarks and audits pass.
  A later PostgreSQL race at `5f11067` exposed an unbound `$resume_input` when
  timer delivery won a manual-resume race. Source `a77678a` sets or clears that
  value on every inline and durable wait; its local grouped timer cases pass,
  with exact-source hosted requalification pending. The checked finite profile
  remains the earlier accepted scope; this is regression requalification of the
  later timer-race correction.
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
  S2-09's finite continuation policy is verified, while in-place migration,
  full proposed criteria and JavaScript remain outside its qualified scope.
- [x] **S2-09** (L) — Define compatible workflow-version migration, crash/replay behavior and recovery diagnostics.
  Verified finite VM01-VM08 continuation/recovery policy at PR head `9578459`.
  Retained definitions continue under the original identity; changed profiles
  and unapproved in-place mappings reject; diagnostics remain bounded and
  owner-authorized. ADK/LangGraph, SQLite/PostgreSQL, process-stop, packaging,
  Windows and hosted gates pass. A086/T44 successful in-place mapping and full
  history remain explicitly unsupported. See the
  [acceptance record](docs/s2-09-version-recovery-acceptance.md).

## Stage 3 — Agents, tools and integrations

- [x] **S3-01** (M) — Complete MCP tools/resources/transports/authentication with pinned official SDKs and integration tests.
  Verified finite Python MCP01-MCP08 profile at exact source
  `af4acd61c200787fb68248b2d180ba69efb8c837`. CI, PostgreSQL, Agent Framework,
  External Sandbox, Security and Docs all passed. Full MCP conformance,
  JavaScript and proposed T29/T30/T31 remain open. See the
  [acceptance record](docs/s3-01-mcp-acceptance.md) and
  [exact-source results](docs/validation-history/pending-batch-results.md#exact-source-qualification-af4acd6).
- [x] **S3-02** (L) — Complete the finite Python profile for workflow-to-agent and agent-to-workflow A2A invocation, exposure, lifecycle and interoperability.
  Verified at exact source `3b6b32994d45bdb4cedb1135720da12fc4fe29ef` after
  all six hosted workflows passed: CI, PostgreSQL CI, Agent Framework CI,
  External Sandbox CI, Security and Docs. The finite profile covers pinned
  A2A SDK 1.2.2 / wire 1.0, advertised JSON-RPC and HTTP+JSON, ADK and
  LangGraph, SQLite and PostgreSQL, bounded lifecycle/recovery, and the named
  authenticated interoperability cases. Durable Task/receipt expiry, push,
  delegated identity, JavaScript and full protocol/proposed criteria remain
  outside this qualification. See the [finite acceptance record](docs/s3-02-a2a-acceptance.md)
  and [exact-source validation](docs/validation-history/pending-batch-results.md#exact-source-qualification-3b6b329).
- [ ] **S3-03** (L) — Complete HTTP/OpenAPI/gRPC/AsyncAPI, function and background-operation adapters as required by the inventory.
  Finite and partial results for sources `923302f`, `31ec0eb`, `237f09f`,
  `0f16415`, `f89a154` and `c3ca7f1`, including their fixes and hosted outcomes,
  remain in the [acceptance plan](docs/s3-03-operation-adapters-acceptance.md)
  and [validation history](docs/validation-history/pending-batch-results.md#partial-source-c3ca7f1).
  The `c3ca7f1` Windows provider timeout exceeded its 30-second bound by one
  floating-point step; the clamp's focused suite passed (62 passed, 6 skipped).
  Its hosted ADK HTTP/JSON skill-resume failure returned
  `invalid_invocation_state`; a focused rerun passed, but the hosted cause was
  not retained. That batch was partial and cancelled during consolidation. A
  later ADK JSON-RPC skill-resume failure exposed startup replay of a waiting
  invocation's initial-delivery row. The dispatcher now settles it under the
  execution lease; five focused reruns passed. A safe `_resume_owned` call-site
  diagnostic regression and 12 inline/reusable-function cases for nonzero exits
  under `code`, `all` and `none` projections are authored and await final-batch
  validation. An eight-case actual-process witness covers configured OpenAPI
  agent-tool dispatch with both a pending receiver commit and a completed outer
  agent-call receipt after durable native suspension; it is also authored and
  unrun. S3-03 remains open; exact-source hosted qualification is pending.
  Background execution stays default-off, and cross-adapter, backend, platform,
  lifetime, packaging and broader hosted acceptance remain open.
- [ ] **S3-04** (M) — Complete executable-operation portability through the existing SandboxManager and proven isolation profiles.
  Captured sources, recovery proofs and awaited internal process controls are
  authored; registry configuration/execution defects now propagate instead of
  being mislabeled as source-policy rejection, with internal-error regression
  cases authored and unrun. Required profile and deployment acceptance remains
  open. See the
  [acceptance plan](docs/s3-04-executable-source-acceptance.md).
- [ ] **S3-05** (M) — Reuse durable protected approvals; add reassignment, escalation, waiting, denial/expiry and resume tests.
  Protected actor identity, atomic decisions, expiry and native recovery are
  authored. The PostgreSQL timestamp migration and f9 database cases passed;
  its Windows timer lane exposed a request-row recovery race before the durable
  barrier. A five-second renewal lease and natural database-clock wait pass
  locally, and the formerly failing hosted Windows case passed at `8183776`.
  The new producer-stop witness replays reassignment/escalation after restart
  and checks the record/audit remain unchanged; its ADK/LangGraph × SQLite/
  PostgreSQL matrix and hosted requalification remain pending. The expired-
  approval fixture now decouples invocation ownership from its short approval
  deadline; independent-SDK requalification also remains pending. See the
  [acceptance plan](docs/s3-05-protected-approval-acceptance.md) and
  [recovery history](docs/validation-history/pending-batch-results.md#protected-approval-recovery-lease-boundary-at-f9cb76f).
- [ ] **S3-06** (L) — Complete identity propagation, credential references, policy/audit/isolation and authorization within existing contracts. Delegated token exchange and consent remain conditional S4-11 work.
  Unpaired Unicode in principal and named-profile references now fails as a
  bounded-reference `ValueError` instead of leaking an encoding exception;
  descriptor regressions cover identity, role, scope, audience and profile.
  Cross-engine/backend and recovery acceptance remains open.
- [ ] **S3-07** (M) — Finish all eight legacy agent patterns as bounded OWS workflows or explicit native extensions.
  Eight finite recipe families and named role bindings are authored; hosted
  acceptance remains open. A focused real-native role-policy test passes on
  SQLite for both principal engines, proving denial before provider dispatch;
  a new ADK/LangGraph × SQLite/PostgreSQL credential regression checks archived
  `from_env` references without a known environment canary. Its injected fake
  model does not resolve the credential. It awaits the final batch; PostgreSQL
  and the full eight-family matrix remain pending. See the
  [acceptance plan](docs/s3-07-agent-pattern-acceptance.md) and
  [timing evidence](docs/validation-history/pending-batch-results.md#windows-timing-suite-follow-up-at-8183776).
- [ ] **S3-08** (L) — Port selected ADK compiler/provider/broker/memory patterns and fixtures against canonical contracts.
  Selected finite adapters and grouped corrections are authored. A new
  cross-engine/backend regression checks direct LLM tool-request pass-through
  without dispatching an undeployed tool; it awaits the final batch. See
  [the acceptance plan](docs/s3-08-selected-adapter-acceptance.md). Qualification remains pending.

## Stage 4 — Advanced features and full verification

- [ ] **S4-01** (M) — Implement versioned native YAML metadata extensions with capability admission; reject unsupported requests.
  The bounded declaration and exact engine-capability gate are authored in
  standard `document.metadata` without changing the official schema; focused
  core tests pass. S4-02's exact LangGraph channel extension is the first
  capability using this gate; broader tracker coverage and hosted
  qualification remain open.
- [ ] **S4-02** (L) — Add declared native channels/reducers, Command/Send, memory, streams, state updates, replay and time travel.
  The first scoped channel/reducer slice is authored for the exact
  `langgraph.channels@1.0.0` extension using private reducer-backed LangGraph
  state. Its focused core (10 cases) and native LangGraph (3 cases) tests pass;
  the final validation batch remains pending and broader S4-02 features remain
  open. See
  [the S4-02 acceptance record](docs/s4-02-native-channels-acceptance.md).
- [ ] **S4-03** (L) — Implement and independently qualify JavaScript/TypeScript; keep Python-only and JS-only differences visible.
  An optional pinned LangGraph/TypeScript scaffold and native smoke test now
  exist under `engines/langgraph-js`; frozen predecessor sources remain
  reference-only. The package does not load or execute OWS definitions yet;
  implement the isolated engine and shared fixture tests before any parity claim.
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
