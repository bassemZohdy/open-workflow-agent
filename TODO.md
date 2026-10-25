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

The [full tracker](resources/compatibility/feature-tracker.json) preserves **658 features, 443 proposed tests and 774 schema constraints**, with Python/JS status and links to tasks below. The corrected consolidated local batch passed 5,004 tests, skipped 3,206 and reported 90.54% core coverage; formatting, Ruff, strict mypy, Markdown links, six lock checks, root/core wheel and source builds, Node tests and production audit passed. This is local evidence only: exact-source hosted CI, PostgreSQL and other skipped native/backend gates remain open, and no task is complete from this batch. See the [corrected batch record](docs/validation-history/pending-batch-results.md#corrected-working-tree-batch--local-validation-before-hosted-requalification). The [migration record](docs/agent-runtime-consolidation.md) distinguishes imported reference material from active code.

Rules: reuse native LangGraph graphs/tasks/checkpoints; start in one container with embedded storage; make external services optional configuration; put task/workflow behavior in workflow YAML/JSON and environment preparation in bootstrap configuration. Versioned native features use custom metadata extensions and explicit capability checks.

Effort: **S** small, **M** medium, **L** substantial. These are relative sizes, not time estimates.

## Historical verified prerequisites — 2026-10-14

Development automation update (2026-10-25): validation is manual by default and
publication separately opt-in. This does not waive acceptance gates or complete
S4-07. See the [cleanup review](docs/development-cleanup-2026-10-25.md) for the
remaining gaps; S3-03 remains the next ordered open task.

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
  outside this qualification. The finite S3-02 profile explicitly excludes
  durable Task/receipt expiry; no retention duration is selected. See the
  [finite acceptance record](docs/s3-02-a2a-acceptance.md)
  and [exact-source validation](docs/validation-history/pending-batch-results.md#exact-source-qualification-3b6b329).
- [ ] **S3-03** (L) — Complete HTTP/OpenAPI/gRPC/AsyncAPI, function and background-operation adapters as required by the inventory.
  Encoded-reference follow-up (2026-10-25): OpenAPI/pure AsyncAPI decode local
  URI fragments once and reject malformed encoding. Common checks pass 309;
  both native engines pass all four new Windows SQLite/PostgreSQL cases with
  supported loop configuration. Windows LangGraph/PostgreSQL now rejects the
  default unsupported loop before service startup. Docker source rebuilds reuse
  embedding assets; fresh package payloads and local adapter installs are checked.
  See the [follow-up evidence and remaining gates](docs/s3-03-operation-adapters-acceptance.md#encoded-reference-and-host-admission-follow-up--2026-10-25).
  Docker follow-up (2026-10-25): 663 Linux checks pass per engine, including all
  114 SQLite/PostgreSQL OpenAPI/gRPC native cases per engine with no mandatory
  skips. Return-mode/receipt checks pass 77 per engine; PostgreSQL tool/resume
  integrations pass four per engine. Both packaged runtimes and the restricted
  Docker sandbox pass their scripts. The controller now uses hashed locked
  dependencies, verified inside its actual image. Common PostgreSQL tests pass
  195 with two inapplicable SQLite skips; four Windows-only controls also pass.
  Whole-source/hosted and broader inventory qualification remain open. See the
  [Docker evidence](docs/s3-03-operation-adapters-acceptance.md#docker-and-postgresql-follow-up--2026-10-25).
  Local follow-up (2026-10-25) fixes malformed OpenAPI reference admission and
  the cleanup's nested-package exclusion; 436 common tests, 57 shared SQLite
  checks per principal engine, eight tool/resume integration checks, the ADK
  diagnostic and 29 build/workflow checks pass. Seven package payloads are
  verified. PostgreSQL, platform/container and hosted qualification remain open;
  see the [local acceptance record](docs/s3-03-operation-adapters-acceptance.md#local-reference-correction--2026-10-25).
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
  diagnostic regression passes locally, as do the 12 inline/reusable-function
  cases for nonzero exits under `code`, `all` and `none` projections in the
  Docker return-mode selections. The eight-case actual-process witness for
  configured OpenAPI agent tools covers a pending receiver commit and a completed
  outer agent-call receipt after durable native suspension; it passes the recorded
  SQLite/PostgreSQL selections. An imported-catalog regression checks that the transformed
  resolver binding forwards the original durable OpenAPI selection and pinned
  catalog reference. Imported resolver calls also retain the invocation's
  granted OpenAPI profile and endpoint bindings while catalog credentials stay
  scoped to catalog fetches; native ADK/LangGraph × SQLite/PostgreSQL regressions
  pass the Docker batch. Exact-source hosted and broader qualification remain
  pending. A credentialed redirect regression verifies that a named bearer
  credential reaches only the selected source
  and never the redirect Location; the common Docker selections pass. S3-03
  remains open. Generic HTTP methods now reject malformed ASCII token values
  (including whitespace, CRLF and non-ASCII) before endpoint resolution or
  transport; type and token-boundary regressions pass the common Docker selections.
  An OpenAPI client-credentials scope-mismatch regression now requires denial
  before secret resolution and network access; it passes the same selections.
  Background execution stays default-off, and cross-adapter, backend, platform,
  lifetime, packaging and broader hosted acceptance remain open.
- [x] **S3-04** (M) — Complete executable-operation portability through the existing SandboxManager and proven isolation profiles.
  Verified finite profile at `d72781f`: Finite Python captured-source registry/admission/recovery and common awaited Python/JavaScript/shell execution, return projections, process controls, source identity and existing selected external sandbox boundaries.
  Background activation remains S3-03; captured-source/background JavaScript, wider container policies, unsupported platform controls and independent JS workflow parity remain excluded.
  See [acceptance](docs/s3-04-executable-source-acceptance.md#exact-source-qualification-d72781f) and [the source-matched results](docs/validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-d72781f).

- [x] **S3-05** (M) — Reuse durable protected approvals; add reassignment, escalation, waiting, denial/expiry and resume tests.
  Verified finite profile at `d72781f`: Finite default-disabled protected-approval v1: authenticated assignee/reassignment/escalation, revisioned denial/expiry/response validation, immutable audit/proof and native/process recovery on both principal engines and databases.
  Delegated consent/identity-platform work remains conditional S4-11; full proposed and independent JS criteria remain separate.
  See [acceptance](docs/s3-05-protected-approval-acceptance.md#exact-source-qualification-d72781f) and [the source-matched results](docs/validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-d72781f).

- [x] **S3-06** (L) — Complete identity propagation, credential references, policy/audit/isolation and authorization within existing contracts. Delegated token exchange and consent remain conditional S4-11 work.
  Verified finite profile at `d72781f`: Existing-contract identity propagation, strict bearer/API-key ingress, original authority, exact policy/resource/credential decisions, task scopes, memory isolation, audit/recovery fences and direct literal credential projection.
  Multi-tenancy, delegated token exchange/consent, encoded-secret taint analysis and hard process isolation are excluded; S4-11 stays conditional.
  See [acceptance](docs/s3-06-identity-security-acceptance.md#exact-source-qualification-d72781f) and [the source-matched results](docs/validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-d72781f).

- [x] **S3-07** (M) — Finish all eight legacy agent patterns as bounded OWS workflows or explicit native extensions.
  Verified finite profile at `d72781f`: All eight bounded OWS recipe families with named roles, original caller/role policy intersection, quotas/tool bindings, protected approval/coordinator lineage and known/uncertain process recovery.
  Arbitrary dynamic teams, recursive autonomous planners, provider-specific native APIs and full proposed/JS coverage are excluded.
  See [acceptance](docs/s3-07-agent-pattern-acceptance.md#exact-source-qualification-d72781f) and [the source-matched results](docs/validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-d72781f).

- [x] **S3-08** (L) — Port selected ADK compiler/provider/broker/memory patterns and fixtures against canonical contracts.
  Verified finite profile at `d72781f`: Selected independently adapted compiler/provider DTO and service patterns, bounded common memory, embedded event codec/admission/ACK seams and original-source/native process recovery.
  Remote broker/vendor interoperability and transport selection guarantees beyond the recorded finite profile remain excluded; frozen predecessor code is not runtime code.
  See [acceptance](docs/s3-08-selected-adapter-acceptance.md#exact-source-qualification-d72781f) and [the source-matched results](docs/validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-d72781f).

## Stage 4 — Advanced features and full verification

- [x] **S4-01** (M) — Implement versioned native YAML metadata extensions with capability admission; reject unsupported requests.
  Verified finite profile at `d72781f`: Versioned native metadata declaration and exact capability admission, required/optional semantics and engine/backend checks with unsupported required requests rejected before mutation; real channel/Command witnesses and ADK rejection.
  This qualifies the declaration/admission boundary, not the wider S4-02 native features or independent JavaScript engine.
  See [acceptance](docs/s4-01-native-extension-acceptance.md#exact-source-qualification-d72781f) and [the source-matched results](docs/validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-d72781f).

- [ ] **S4-02** (L) — Add declared native channels/reducers, Command/Send, memory, streams, state updates, replay and time travel.
  A later bounded native Send candidate dispatches non-competing forks through
  the actual SDK while core retains join/end/error/receipt policy. Thirteen
  focused cases include physical SQLite/PostgreSQL recovery; Linux/full-source
  and frozen default-fork regression qualification remain pending.
  A bounded required `langgraph.command@1.0.0` candidate now uses real SDK
  Command updates and per-scope transitions without a new public payload.
  Required/optional root/nested routes and ADK admission rejection pass locally.
  Nested-reader checkpoint privacy is repaired and forged stored bindings reject
  before mutation on SQLite/PostgreSQL. Four Command/default × root/nested
  SQLite process-stop cells pass. Full qualification and the remaining Send,
  memory, streams, state-update, replay and time-travel scope stay open.
  The first scoped channel/reducer slice is authored for the exact
  `langgraph.channels@1.0.0` extension using private reducer-backed LangGraph
  state. Its focused core (10 cases) and native channel (3 cases) tests pass.
  A LangGraph × SQLite/PostgreSQL process-stop regression now checks the
  committed reducer delta survives restart without duplication; the SQLite
  selection passes locally and the PostgreSQL selection awaits hosted CI.
  A `last_value` integration regression exercises exact extension admission,
  a writer task, and a later reader binding; its focused local selection passes.
  A native run/pause/resume regression checks channel values across a wait
  boundary and remains pending its engine-specific selection. The SQLite
  process-stop case passes locally; PostgreSQL and exact-head hosted
  qualification remain pending. Broader S4-02 features remain open. See
  [the S4-02 acceptance record](docs/s4-02-native-channels-acceptance.md).
- [ ] **S4-03** (L) — Implement and independently qualify JavaScript/TypeScript; keep Python-only and JS-only differences visible.
  A later literal-fault candidate adds standard inline `raise.error` and four
  exact shared fault cases. Node passes 48 package tests and each actual Python
  SDK passes all 13 shared success/fault cases. A format-validation difference
  remains explicit; full current-source/Docker qualification is pending.
  An experimental native literal execution component now supports `set`, nested
  `do` and fixed scope transitions. Nine exact fixtures run on Node and Python;
  unsupported features reject before native graph construction. It has no public
  invocation identity, persistence, recovery, expressions or calls and is not a
  full engine adapter. Windows Node 24 passes 43 package tests; further actual
  SDK/Linux qualification and the broader task scope remain required.
  An optional pinned LangGraph/TypeScript scaffold and native smoke test now
  exist under `engines/langgraph-js`. Its loader validates YAML/JSON against the
  SHA-256-pinned official OWS 1.0.3 schema and sanitizes malformed-YAML errors;
  local Node tests and the production dependency audit pass. The loader now
  rejects source above 1 MiB measured as UTF-8 before parsing, with boundary
  and multibyte cases included in the consolidated local batch. A second loader slice now
  mirrors Python admission rejection for duplicate YAML/JSON keys, cyclic
  aliases, implicit timestamps and non-finite values; those cases were included
  in the consolidated local batch. Python and TypeScript now consume shared admission fixtures,
  including rejection of numeric/boolean YAML mapping keys and acceptance of
  equivalent string-keyed JSON, with error-category parity cases. The Node 24
  package suite (10 tests) and production dependency audit passed locally for
  the consolidated batch; exact-head hosted qualification remains pending. The
  package's wider OWS compilation/execution and execution-adapter parity remain
  unimplemented; complete those and independently qualify the engine before claiming full
  parity. Frozen predecessor sources remain
  reference-only.
- [ ] **S4-04** (L) — Automate official CTK plus all proposed tests, schema constraints, parity and cross-engine outcome checks.
  A finite comparison candidate captures the 13 shared literal success/fault
  outcomes from actual ADK, LangGraph Python and Node, then requires matched
  source/SDK/runtime/fixture/schema evidence in CI. Native capture and full-source
  qualification remain pending; no proposed/schema criterion is promoted.
  `resources/compatibility/coverage-bridge.json` now inventories all 443
  proposed test IDs and 774 schema-constraint rows. Every row remains explicitly
  unmapped and unexecuted because partial overlap is not evidence of equivalent
  coverage. An integrity test checks exhaustive IDs and requires live selectors
  and retained evidence before any row can be promoted. The bridge now includes
  reasoned blocker counts (443 tests, 774 constraints) guarded against row
  status drift. Integrity assertions also retain source verification guidance
  on all 774 schema rows; the AST selectors passed in the consolidated local
  suite. These checks do not count as schema coverage. Selector promotion rejects `skip`, `skipif`, and
  `xfail` markers, including module-level skip markers; its AST regression also
  passed in the consolidated local suite and does not establish criterion
  coverage. Full CTK automation and parity qualification remain open. See
  [the coverage bridge](resources/compatibility/README.md).
- [ ] **S4-05** (M) — Complete execution history, tracing, lifecycle metrics, correlation, provenance and replay diagnostics.
  A later candidate adds bounded opt-in common invocation/task tracing, generated
  version-00 lifecycle contexts, optional HTTP propagation and verified digest
  provenance in authorized recovery diagnostics. Local actual-engine/store
  recovery and API/privacy checks pass; current-source qualification is pending.
  Opt-in bounded persistent lifecycle history is now authored with local
  SQLite/PostgreSQL physical-stop, concurrent-writer and real API replay/privacy
  checks. Four actual principal-engine/backend restart cases retain original
  events with one business dispatch. Linux and full-source qualification remain
  required; this diagnostic history is not recovery authority or an atomic audit.
  The current batch adds a bounded task-duration histogram labeled only by
  engine and terminal outcome, with privacy/outcome tests passing locally and
  API/configuration docs validated. Durable cross-process history, trace-context and
  causation propagation, richer provenance, and replay diagnostics remain open.
  An idle-child operator cancellation now also carries the persisted parent
  invocation and task reference into its lifecycle event; its focused
  correlation selection passes locally. Terminal workflow metrics derive the
  outcome from the fixed event type rather than an untrusted status field, with
  a high-cardinality-label regression passing in the consolidated local suite. Arbitrary or
  non-string task event statuses now map to the fixed `unknown` label, with a
  regression proving no user-controlled metric series is created; the
  consolidated local suite passes. Exact-head hosted qualification remains
  pending.
  See the [observability acceptance slice](docs/s4-05-observability-acceptance.md).
- [x] **S4-06** (M) — Run matched latency/CPU/memory/concurrency/persistence/recovery benchmarks; publish measured results.
  Verified finite profile at `d72781f`: Retained matched ADK/LangGraph x SQLite/PostgreSQL 30/5/8/3 benchmark report with latency, invocation CPU, lifetime peak RSS, concurrency, creation/footprint and timed service restart/resume correctness.
  Runner-specific measurements are not capacity guarantees; persistence I/O is included in latency, PostgreSQL size is whole-service and recovery timings use same-process reinitialization. Physical-stop proof remains separate.
  See [acceptance](docs/benchmarks.md#exact-source-qualification-d72781f) and [the source-matched results](docs/validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-d72781f).

- [ ] **S4-07** (M) — Keep CI, builds, releases, capability reports and rollback tied to verified versions and acceptance gates.
  A new release-record candidate combines actual pushed runtime/controller
  digests into one source/version/run-matched manifest, rejects incomplete or
  mutable sets, and requires it before attaching a GitHub Release asset. Local
  validator/workflow checks pass; no publication or rollback is performed.
  User-requested cleanup is verified at `3f568e5`: PR/main trigger separation, independent
  Windows/native/image checks, one root contract execution, optional Framework
  coverage for common changes, and stale-PR cancellation. Required release
  gates were preserved and all six configured workflows passed at that checkpoint.
  The later hosted checkpoint failed, as recorded above. Release version
  equality now covers the root, core, ADK, LangGraph and both published
  controller packages; its test guards metadata agreement and workflow
  coverage. Publication now waits for exact-head CI, PostgreSQL, External
  Sandbox, Security and Docs results; Agent Framework remains optional. These
  guards and revised main-push triggers pass local workflow tests. At pushed
  source `0d812b2`, PostgreSQL, Agent Framework, External Sandbox, Security and
  Docs passed; CI failed the Windows CPU-quota timing case and one Linux stdio
  child start. The CPU fixture now has a 20-second operation budget and
  25-second outer bound. The stdio failure was sanitized before its underlying
  OS error was retained, so current-source hosted requalification remains
  pending; see the [validation history](docs/validation-history/pending-batch-results.md#hosted-outcome-for-source-0d812b2).
  CI now includes a Node 24 job for the isolated
  JavaScript schema loader. A regression now guards source/version/exact-commit
  OCI labels and retained SBOM/provenance generation on both release image
  paths; the guard passed in the consolidated local suite. The older root-quality run also
  exposed a hosted child-start timeout in a sandbox control-fence test; its
  startup/deadline margin is widened and the focused two-case regression passes.
  At source `52abe1f`, Docs passed (run `39525850419`) while Security failed
  (run `39525850472`) because pip-audit found fixable certifi and Pillow
  advisories across Python environments. The current authored remediation
  updates certifi and constrains FastEmbed environments to Pillow
  `>=13.0.0,<14`. The audit command now uses pip-audit's supported
  `--disable-pip` path with the fully pinned export; a focused local audit
  completed and retained both FastEmbed and Pillow in its report; the locked
  Pillow import smoke also passes locally. Exact-source hosted Security
  requalification remains pending. External Sandbox (`39525850438`)
  also passed. CI (`39525850490`), Agent Framework (`39525850489`) and
  PostgreSQL (`39525850501`) were still in progress at the latest check; these
  exact-source statuses are a snapshot. Do not treat the old source result or
  the focused local audit as a hosted green gate.
  CI now removes only the duplicate `test_native_sqlite_inspection` invocation
  from the `windows-mcp` lane; that check remains in `windows-expressions` and
  `native`. Workflow cleanup and its regression checks passed in the
  consolidated local suite. Exact-head hosted requalification, including the
  Security workflow's pip-audit resolver adjustment, remains pending.
  Remaining S4-07 release/rollback and broader capability/version acceptance
  stay open. See the [release gate record](docs/s4-07-release-gates-acceptance.md).
- [x] **S4-08** (M) — Decide and document old openworkflow_adk import/CLI/package compatibility; port useful developer tooling.
  Verified finite profile at `d72781f`: Canonical package/import and CLI policy, no predecessor shims, source/qualified-OCI delivery, disabled Python index publication, separate-environment manual migration and real owa validate schema/compiler checks.
  No package transfer/index publication, managed consumer migration or checkpoint migration was performed; future publication requires ownership/version/consumer evidence.
  See [acceptance](docs/s4-08-compatibility-decision.md#exact-source-qualification-d72781f) and [the source-matched results](docs/validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-d72781f).

- [ ] **S4-09** (L) — Preserve ENGINE-5 through ENGINE-8 as optional Agent Framework hardening; activate only after an explicit support decision and require isolated native durability, security, and release acceptance.
  Activation needs a named user need, deployment target, compatibility promise,
  and rollback owner. Until then, keep the adapter optional and CI/evaluation
  only; see the [production-readiness plan](docs/agent-framework-production-readiness.md).
- [ ] **S4-10** (L) — Preserve EVENT-1 through EVENT-5 as conditional external event integration; select a real consumer and transport plus delivery, security, and operational contracts before broker, AsyncAPI, or push implementation.
  The event track is not a runtime task until a consumer and deployment
  requirements are recorded; see the [event integration readiness plan](docs/event-integration-readiness.md).
- [ ] **S4-11** (L) — Track multi-tenancy and RFC 8693/delegated consent as conditional identity-platform work; start only on concrete requirements and use standards-based trust without making OWA an identity provider.
  Multi-tenant isolation and delegated-user identity remain deployment/identity
  platform responsibilities until a concrete use case defines owners and trust
  boundaries; see [security decisions](docs/protocol-security-decisions.md).

## Completion rule

Close a task only when its mapped features have tested outcomes, failure/restart behavior where applicable, and recorded evidence for each supported backend. Unsupported or conditional features stay visible. Existing CTK profile results are not full OWS conformance. Do not label copied source or proposed tests as implemented capabilities.

Earlier completed release/readiness work and deferred details are preserved in [the previous backlog](docs/backlog-before-consolidation-2026-10-11.md). Optional Microsoft Agent Framework hardening, external event transports, push notifications, multi-tenancy and delegated identity remain conditional; tracking them does not activate them automatically.

## Deferred tasks

No active backlog task is currently deferred. Actionable validation failures
remain with their original task. Move an entry here only when the blocked-task
rule applies, preserving its unchecked scope and the full deferral record.
