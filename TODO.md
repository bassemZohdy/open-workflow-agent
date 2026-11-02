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
publication separately opt-in. This does not waive acceptance gates; S4-07
later qualifies its finite profile at `f66cd23`. See the [cleanup review](docs/development-cleanup-2026-10-25.md) for the
remaining gaps; S4-02 is now the next ordered open task.

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
- [x] **S3-03** (L) — Complete HTTP/OpenAPI/gRPC/AsyncAPI, function and background-operation adapters as required by the inventory.
  Verified finite profile at `f66cd23`: Admitted shared HTTP/OpenAPI/gRPC and pure AsyncAPI operations, supported functions and published opt-in internal SQLite Python background jobs, with common source/receipt/ownership/deadline controls.
  External broker delivery remains conditional; PostgreSQL/external background supervisors, background JavaScript and unadvertised executable/container profiles remain unsupported.
  See [acceptance](docs/s3-03-operation-adapters-acceptance.md#exact-source-qualification-f66cd23) and [the source-matched results](docs/validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-f66cd23).

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
  Verified finite components cover declared channels/routing, configured native
  filtered/semantic memory, public values/updates streams and state/history
  reads. The configured original-owner root update/recovery source profile is
  merged in PR #92 (`6ec11f1`), including bounded HTTP work, cancellation draining,
  restart-safe proof inspection, configuration-only examples and continuation;
  see the [profile guide](docs/native-state-updates.md).
  PR #93 (`b363f10`) adds immutable replay/branch policies, explicit effect
  acknowledgement and selected-checkpoint preflight with historical root/scope
  closure and original/current authority. The private SDK writer now commits
  that closure and native proof atomically under a pre-reserved target lease.
  A private common registry records lineage and source-backed receipt mappings;
  an explicit scoped gate checks effect policy at the common action boundary.
  The current development branch adds proof inspection and a root-only private
  replay projection with source-backed receipt remapping and scoped effect
  checks. A focused SQLite/PostgreSQL run passes 16 cases, including real native
  replay under both effect policies; this is not public invocation admission.
  A newly initialized engine can reconstruct the private binding and continue
  a manually staged root replay under either effect policy on SQLite and
  PostgreSQL. The development branch also atomically records a private target
  reservation, reopens its request, reconstructs the historical basis, and
  recognizes an already committed fork. A private coordinator now reconciles
  reservation, admission, native fork, lineage binding and continuation; 14
  SQLite/PostgreSQL coordinator and real process-interruption cases pass.
  A separate SQLite/PostgreSQL process-kill test during a model call fails closed
  without repeating the uncertain effect.
  The private root coordinator also replays from a later completed-task prefix
  (locally verified on SQLite and PostgreSQL for one- and two-task prefixes).
  Broader checkpoint/scope remapping and full lifecycle gate installation remain
  open. The finite public replay and branch profiles are described below; they do
  not establish broader attribution or scope support. Storage atomicity alone does not make a
  business fork safe to resume. A private root branch projection now replaces
  schema-validated data at the last completed root task and is exercised through
  a manually staged native fork, receipt binding and continuation on SQLite and
  PostgreSQL. Private reservation now supports exact retry, fresh-service
  reconstruction and a process stop immediately after reservation on both
  stores. An opt-in public root branch source profile now admits and continues
  attributed branches, with local SQLite/PostgreSQL HTTP and process-stop
  evidence. Owner state views now also verify retained lineage before projecting
  branch and replay targets. Root replay and branch now also preserve a bounded
  closure of completed nested scopes whose tasks were visited once per occurrence; local
  SQLite/PostgreSQL evidence covers two nested levels, effect policies, proof
  damage, fresh-engine inspection and injected interruption after the fork.
  Declared append and last-value channel snapshots also survive root lineage,
  including a completed nested scope, with local reducer/restart evidence.
  Native Command destinations now survive the fork, with retained SDK scheduling
  checked against the attested next node and local jump/tampering evidence.
  Verified forward routes through root `set`/`call` tasks may skip tasks before a
  selected checkpoint; ambiguous backward attribution fails before reservation.
  The root lineage source profile also permits bounded, non-competing native
  Send fan-outs, preserving earlier branch effects and applying the target's
  effect policy to later ones.
  Broader nested scope and real process-stop qualification remain open.
  The frozen public branch PostgreSQL job passed;
  broader profiles and full hosted qualification remain open; see the
  [lineage record](docs/s4-02-native-lineage-acceptance.md#public-root-branch-source-profile--2026-11-02)
  and [operator guide](docs/native-state-branch.md).
  Frozen `479906c`
  passes 538 native combined cases per platform, zero skips and 1,288 unchanged
  Linux inputs; unchanged core/ADK inputs at `81691b7` pass 881/26 cases. See
  [lineage acceptance](docs/s4-02-native-lineage-acceptance.md) for source-matched
  qualification and retained development failures/corrections.
  Continue broader native profiles and replay/time-travel branches, preserving
  new business identity, immutable lineage and explicit effect policy. Broader
  memory T51/T68, stream modes/T50, native scope, T45/T49 and full S4-02 acceptance
  remain open, as does hosted qualification of the direct-child routing/scope
  process-stop repair. Preserve common authority, ownership, receipts and private
  checkpoints. See [channels](docs/s4-02-native-channels-acceptance.md),
  [memory](docs/s4-02-native-memory-acceptance.md),
  [streams](docs/s4-02-native-streams-acceptance.md),
  [state controls](docs/s4-02-native-state-acceptance.md),
  [public replay admission design](docs/s4-02-public-replay-admission-design.md) and
  [opt-in replay route](docs/native-state-replay.md) (HTTP tests pass on SQLite and
  PostgreSQL, including per-source and depth limits; a configuration-only example, crash/restart through
  the route, cancellation of a running target, graceful shutdown, a changed policy and
  revocation during a running replay; hosted Linux qualification at a frozen commit is
  [recorded](docs/validation-history/s4-02-public-replay-hosted-qualification.md)),
  [retained component history](docs/validation-history/s4-02-component-notes.md)
  and the [integration record](docs/validation-history/main-integration-2026-10-25.md).

- [ ] **S4-03** (L) — Implement and independently qualify JavaScript/TypeScript; keep Python-only and JS-only differences visible.
  The optional TypeScript package admits official OWS definitions and executes
  literal set/do/raise, try/catch (literal error filters), non-competing fork
  jq 1.8.2 `set` expressions, `switch` and `for` through actual native graphs. One hundred eleven shared
  success/fault fixtures match locally across ADK, LangGraph Python and Node; the
  Node package has 155 tests. This remains a component, not a complete engine adapter.
  Public identity, durable services, expressions, calls and full runtime behavior
  remain missing. Keep the format-validation difference explicit. See the
  [execution scope](docs/s4-03-javascript-execution-acceptance.md),
  [comparison scope](docs/s4-04-literal-parity-acceptance.md) and
  [integration record](docs/validation-history/main-integration-2026-10-25.md).

- [ ] **S4-04** (L) — Automate official CTK plus all proposed tests, schema constraints, parity and cross-engine outcome checks.
  The 13-case actual native outcome comparison requires one source, exact SDK/
  runtime versions and matching fixture/schema bytes; all 13 outcomes pass the
  hosted aggregate at `5310f5a`. Existing selected Portable Profile CTK automation does not
  cover the full proposed inventory. All 443 proposed tests remain unmapped; 658 of the
  774 schema constraints (required, closed-object, additionalProperties, default, enum, const, bound, type, pattern,
  composition and `$ref` rows) are
  executed in isolation against the packaged schema with Python only
  ([required](docs/validation-history/s4-04-schema-required-constraints.md) and
  [value](docs/validation-history/s4-04-schema-value-constraints.md) evidence),
  the other 116 remain unmapped, and none is verified. Its integrity
  checks are not conformance evidence. See the
  [comparison scope](docs/s4-04-literal-parity-acceptance.md) and
  [coverage bridge](resources/compatibility/README.md) and
  [schema-constraint worklist](docs/s4-04-schema-constraint-worklist.md) and
  [proposed-test feasibility](docs/s4-04-proposed-test-feasibility.md).

- [x] **S4-05** (M) — Complete execution history, tracing, lifecycle metrics, correlation, provenance and replay diagnostics.
  Verified finite profile at `f66cd23`: Bounded persistent sanitized lifecycle history/replay, opt-in invocation/task spans and generated contexts, optional HTTP propagation, low-cardinality metrics and authorized common provenance/recovery diagnostics.
  History is diagnostic, not an atomic audit or resume authority. Incoming trace propagation, vendor exporters, durable spans and native checkpoint readiness inspection are not implemented.
  See [acceptance](docs/s4-05-observability-acceptance.md#exact-source-qualification-f66cd23) and [the source-matched results](docs/validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-f66cd23).

- [x] **S4-06** (M) — Run matched latency/CPU/memory/concurrency/persistence/recovery benchmarks; publish measured results.
  Verified finite profile at `d72781f`: Retained matched ADK/LangGraph x SQLite/PostgreSQL 30/5/8/3 benchmark report with latency, invocation CPU, lifetime peak RSS, concurrency, creation/footprint and timed service restart/resume correctness.
  Runner-specific measurements are not capacity guarantees; persistence I/O is included in latency, PostgreSQL size is whole-service and recovery timings use same-process reinitialization. Physical-stop proof remains separate.
  See [acceptance](docs/benchmarks.md#exact-source-qualification-d72781f) and [the source-matched results](docs/validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-d72781f).

- [x] **S4-07** (M) — Keep CI, builds, releases, capability reports and rollback tied to verified versions and acceptance gates.
  Verified finite profile at `f66cd23`: Source/version matched validation and image gates, immutable matched release-image manifest generation and required assembly, explicit operator rollback policy and separately opt-in automation.
  No publication, deployment, registry/index ownership claim, automatic rollback/checkpoint migration or optional-engine production target is performed or qualified.
  See [acceptance](docs/s4-07-release-gates-acceptance.md#exact-source-qualification-f66cd23) and [the source-matched results](docs/validation-history/ready-tasks-2026-10-25.md#exact-source-qualification-f66cd23).

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
