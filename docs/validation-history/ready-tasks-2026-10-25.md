# Ready-task qualification — 2026-10-25

The user requested completion of the open backlog and explicitly kept S4-09,
S4-10 and S4-11 conditional. Those tracks remain inactive. The 14 ready task
entries retain their original scope and gates; this record does not close them.

## Publication candidate aab518e — focused results and active hosted run

Frozen source `aab518ef0922c251533f78d956d79d090daf07a7` rebuilds both production
images with matching full revision labels. Both packaged JavaScript sandbox
gates pass. The Linux SDK/database/history/background selection passes 86 ADK
cases (24 explicit opposite/unconfigured-profile skips) and 178 LangGraph cases
(28 explicit skips). Both selected SDKs run their physical background workers
without overriding the published qualification flag. All 1,154 frozen source
inputs remain byte-identical. Raw reports and manifests are retained under
`tmp/javascript-sandbox-20261025/aab518e/`.

Hosted runs CI `40182145295`, PostgreSQL `40182145195`, Framework `40182145293`,
external sandbox `40182145269`, security `40182145144` and docs `40182145298`
all use that exact source. Framework, external sandbox, security and docs have
passed; CI/PostgreSQL remain active at this recording. The CI Windows expression
job fails one stale expectation: 693 pass, 52 skip, one fails because the test
still expects an explicitly enabled published background profile to be rejected.
The correction explicitly sets the release gate false for this negative test;
actual publication and physical recovery tests continue to use the real default.
Its focused sandbox validation/runtime selection passes 55 with 12 unconfigured
PostgreSQL skips. This partial run cannot close S3-03 or a later candidate task.
Other active jobs are allowed to finish so their results remain reviewable.

## Frozen baseline

Commit `6f9ef5d0d8ea24a75cddca1b2b57060ee546f4bb` captures the preceding development
cleanup and reference/admission fixes. Full independent ADK/LangGraph contract,
adapter and selected CTK suites run against its immutable Linux source, with
actual PostgreSQL and complete Git history for archived-source recovery tests.
The Windows root suite uses its own source copy and locked environment. Results
remain pending until each process exits and its report is verified. The frozen
Windows root run subsequently exits zero with 5,072 passes, 3,226 expected
environment/platform skips and 90.56% coverage. Its input manifest remains
unchanged. This qualifies that historical root source only; it predates the
dependency, credential-fixture and JavaScript corrections below.

Two initial Windows runs were deliberately stopped: the first used the live
working directory; the second used the frozen source with installed editable
metadata pointing to the live checkout. The latter correctly failed recovery
provenance. Installing the frozen checkout's own locked environment resolves
that mismatch; the three failed environment-smoke cases then pass. These partial
runs and failures are retained, and are not passing qualification evidence.

The initial Linux runs were interrupted after identifying test/environment
corrections: ADK retained 388 passes, 16 skips and three failures; LangGraph
retained 669 passes, 26 skips and six failures. No aggregate success is claimed.
Some cross-adapter fallback cases correctly rejected the production-wheel test
environment, which lacked CI's explicit editable-root provenance. The next
test-only image setup installs that root at build time and checks both adapter
profiles before running the suite. Production image provenance and runtime
recovery guards remain unchanged.

The initial native ADK run found a stale test expectation that a newly created
handle had status `created`; the public contract initializes it as `running`.
The corrected test compares the full common SQLite dump before/after unsupported
extension admission, along with no native file or provider call. It passes
against the actual SDK. No invocation-state contract or admission behavior is
changed.

## Dependency corrections

The initial seven-profile Windows advisory scan fails all five runtime profiles
and passes both controllers. It identifies FileLock/PyPDF advisories and older
OpenTelemetry API versions in ADK/optional Framework, plus the PostgreSQL
checkpoint namespace advisory in LangGraph. The advisory's upstream record also
identifies the SQLite checkpoint version as affected.

The correction uses PyPDF `6.21.2`, a resolver constraint of FileLock
`>=4.2.1,<5` (locks resolve `4.2.4`), LangGraph SQLite checkpoint `3.1.2`,
PostgreSQL checkpoint `3.1.3` and their required common checkpoint `4.3.0`.
Optional Framework resolves OpenTelemetry API `1.45.1` without overriding its
dependency bounds.

ADK `2.7.1` caps both OpenTelemetry API and SDK at `1.42.1`; even the reviewed
stable `2.13.0` metadata caps them below the fixed API. The ADK environment
therefore pairs exact API/SDK `1.45.1` through an explicit compatibility override,
retaining its existing ADK version. Native/backend compatibility must be proven
before this override qualifies a release. The independent lock files remain
separate; no package installation is added to runtime startup or sandbox work.

All seven corrected Windows dependency exports pass the existing strict audit
policy, without ignored advisories or removed optional dependency families.
The corrected native adapter selections pass 42 ADK tests and 69 LangGraph tests;
the latter has seven unconfigured PostgreSQL skips which are not acceptance for
that backend. Current-source PostgreSQL, full-suite, image and hosted gates still
remain required.

The real PDF knowledge fixture now creates a valid text page and checks actual
parser ingestion and retrieval with a deterministic local embedding. It passes
with the patched parser; the security/build/knowledge selection passes 40 tests.
Strict mypy passes four CI and 136 runtime/adapter modules. Ruff, changed-file
formatting and documentation links pass.

## Native credential witness correction

The first native role-canary fixture was denied before credential resolution:
the shared caller policy deliberately has no `credential.resolve` grant. Its
provider also returns a JSON string, while the selected recipe requires an
object with an `answer` field. The corrected witness grants only Alice access
to that one named model secret and uses the standard `output.as` JSON transform,
preserving the recipe's existing output schema. Lifecycle reads authenticate
through the actual configured API profile and an exact `events.read` grant.

The final witness also runs without the credential grant, where neither secret
resolution nor provider dispatch may occur. Actual SDK checks pass all eight
ADK/LangGraph × SQLite/PostgreSQL × allow/deny cases with the patched libraries.
They inspect retained common rows, the archive, real native checkpoint rows,
lifecycle events, logs, public results and the authenticated lifecycle response
for the literal canary. This is direct-echo protection, not encoded-secret or
general information-flow proof. Earlier failed reports remain retained.

The complete in-process eight-family pattern suites pass 60 tests per principal
SDK against both databases with the updated fixture and patched libraries.
The source `88ca0f5` hosted checkpoint passed Security, Docs, External Sandbox
and optional Framework. PostgreSQL passed nine jobs, including all matched
measurements and aggregation, but each principal operation job failed only the
uncorrected positive canary fixture (240 passed, one failed per SDK). CI is
complete and failed only that same uncorrected SQLite canary in each principal
native suite and the Windows timer lane. The ADK native suite has 1,449 passes,
LangGraph 1,511, and the Windows timer lane 742; each has one failure. Root
quality passed 5,069 tests with 3,230 environment/profile skips and 90.14%
coverage. The other 18 CI jobs passed. These failures are preserved and do not
qualify the candidate; the following source must re-run the exact-source gates.

## Matched report identity correction

The retained `88ca0f5` four-cell report records runner name `local` because the
harness read the nonstandard `GITHUB_RUNNER` variable. It now prefers the
standard `RUNNER_NAME`, retains the explicit legacy fallback and identifies a
local run only when neither is present. Five parameterized cases cover those
paths; the original raw report remains unchanged. The benchmark aggregation and
runtime helpers now pass strict mypy, which is included in the Linux root gate.
The benchmark/role selection passes 55 tests with 25 unconfigured PostgreSQL
skips; actual PostgreSQL credential checks are recorded separately above.
Fresh source-matched measurements are still required after this correction.

Source `67279ed` fails the root job before tests: the new benchmark mypy command
imports the optional Psycopg module, but that root environment selects knowledge
only. Job `121106101973` reports `import-not-found` for `psycopg`; earlier core/CI
typing passes. The corrected benchmark type gate explicitly selects the existing
`postgres` extra, retaining PostgreSQL as an optional runtime dependency. This
is a CI environment correction, not ignored typing or relaxed coverage. The
remaining `67279ed` jobs retain their own outcomes; this source is not an
aggregate acceptance checkpoint.

Its Windows operation job `121106101729` also fails one cancellation/takeover
case (1,059 passed, 475 skipped). The first cancel observation is `unknown`;
the log does not retain authenticated state/heartbeat evidence to identify why.
The fixture now preserves the submitted control bytes and requires a later
authenticated terminal/cancel acknowledgement, without publishing the control
again. A deterministic first-unknown observation and the stale-control negative
case pass locally (three cases). Permanent unknown state still fails the bounded
wait. Runtime heartbeat bounds, control authorization and original deadlines
are unchanged; this is not proof that every unknown outcome is recoverable.

The JavaScript addition is recorded in the
[executable acceptance record](../s3-04-executable-source-acceptance.md).
Its packaged image/source baseline is `a6a95cd`. The following test/CI corrections
give core and shared fixtures distinct module names, preserve the PostgreSQL
job's original Python selection in one `env` mapping and guard against duplicate
workflow keys. The new physical OS-stop cases retain full known/uncertain proof;
their added run time has a larger finite CI aggregate budget, without changing
operation, owner-lease or workflow deadlines. All 8,326 pre-process-addition tests
collect together after the filename correction. No passing checkpoint is inferred
from collection or from the separate image run.

Dependency/SDK changes intentionally change recovery-profile identity. They do
not grant in-place migration of existing invocations. Retained invocations need
their original matching runtime/lock profile; this record authorizes no rollout,
old-runtime removal or publication.

Sources: [FileLock advisory](https://github.com/advisories/GHSA-v4x8-3wr5-3wq8),
[PyPDF advisory](https://github.com/advisories/GHSA-6c7f-9265-g8fw),
[checkpoint advisory](https://github.com/advisories/GHSA-6hmw-9432-67qq),
[OpenTelemetry advisory](https://github.com/advisories/GHSA-xcq7-p3pf-p88h),
[ADK 2.7.1 metadata](https://pypi.org/pypi/google-adk/2.7.1/json).

Local logs, JUnit files, source hashes and both old/corrected advisory reports are
retained under `tmp/completion-6f9ef5d/`. Passing focused selections are not a
whole-source or full-feature qualification.

## Final source 67279ed and source df6b725 follow-up

The final `67279ed` CI outcome is 18 successful jobs and three failures: the
benchmark typing environment and Windows control-observation fixture described
above, plus ADK reaching its 40-minute suite budget at 99% (exit 124). No assertion
failure was reported before that timeout. PostgreSQL, optional Framework,
External Sandbox, Security and Docs all passed. A timed-out suite is not passing
acceptance. The next source uses a finite 45-minute native-suite budget to include
the added physical JavaScript recovery cases; operation and ownership deadlines
remain unchanged.

At `df6b725e4f096df2e361c715c646e64b5f031be1`, optional Framework, External
Sandbox, Security and Docs pass. Both production-image gates pass packaged
JavaScript execution. Root has 5,088 passes, four JavaScript failures, 3,251
skips and 90.11% coverage; LangGraph native has 1,513 passes, four JavaScript
failures and 1,499 skips. Each PostgreSQL operation job has 242 passes, four
JavaScript failures and 183 skips. Both matched benchmark jobs and their
four-cell aggregate pass. CI's remaining jobs retain their own final statuses;
this partial record is not an aggregate green result.

The new JavaScript failures report Linux startup aborts (exit -6), followed by
missing business-commit proof or bounded readiness timeout. A controlled Linux
probe under a dedicated non-root UID reproduces exit -6 with 49 existing user
threads and `RLIMIT_NPROC=32`; Node reports failure creating a startup thread.
The same probe succeeds with quota 128. This supports a shared-UID quota
explanation, but the hosted failure logs do not retain the runner's thread count.
Shared-host JavaScript fixtures now explicitly omit that quota. Production
default of 64 is unchanged; the packaged image gate explicitly selects and
asserts a 32-process quota under a dedicated arbitrary UID, with independent
Docker PID/memory limits. Isolated contract runners also require the default
quota of 32 through `OWA_TEST_ISOLATED_PROCESS_USER=1`. No secrets, environment or
output controls are relaxed.

Separately, frozen local source `df6b725` passes 19 tests per SDK in Docker
(nine explicit opposite-SDK/platform skips), including both databases, known and
uncertain JavaScript process stops, physical interpreter drift, and both control
observation variants. All 1,139 input hashes remain unchanged. These test images
extend the `a6a95cd` production images; runtime code, locks and metadata match
between those commits. This remains focused local evidence, not an exact-source
production-image rebuild. Reports and the thread-quota probe are retained under
`tmp/javascript-sandbox-20261025/`.

The first `4866840` local image run built both images but rejected before
invocation because the new fixture asserted quota 32 without selecting it (the
real default is 64). The image fixture now selects 32 explicitly. The earlier
failed logs remain retained; this correction changes no application default.

## Frozen source d72781f local Docker follow-up

Both freshly rebuilt production images at
`d72781f86053a7fd77e7b9df7c3a74c262923136` pass the packaged JavaScript gate
with an explicitly selected process quota of 32, dedicated arbitrary UID,
read-only root, no network and independent Docker memory/CPU/PID bounds. Both
OCI revisions contain the full source commit. Independent test images built
from these production images pass 25 ADK cases (nine opposite-SDK/platform
skips) and 117 LangGraph cases (13 explicit skips). The LangGraph selection
includes real Command/default routing, nested reader privacy, actual SDK
checkpoint damage, and all eight routing/reader/database physical-stop cells.
The JavaScript selection covers both real databases, original-source recovery,
uncertain-action non-relaunch, interpreter drift and common physical controls.
All 1,140 frozen inputs remain byte-identical. Local JUnit, logs and the final
input report are retained under `tmp/javascript-sandbox-20261025/d72781f/`.

The final preceding `df6b725` CI outcome is 18 successful jobs and three failed
jobs, all attributable to the four JavaScript witnesses in root and each native
suite. ADK has 1,451 passes, four failures and 1,527 skips, completing within
41 minutes rather than timing out. Its exact-source PostgreSQL operation
failures are recorded above. No new aggregate green result is inferred.
The `4866840` checkpoint was superseded after the explicit image-quota correction;
its cancelled jobs do not qualify it. All six `d72781f` workflows are dispatched
against the full matching commit; their final outcomes remain pending.

## Exact-source qualification d72781f

All six workflows completed successfully at
`d72781f86053a7fd77e7b9df7c3a74c262923136`:

| Workflow | Exact-source run | Result |
| --- | --- | --- |
| CI | [40178378634](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40178378634) | All 21 jobs pass |
| PostgreSQL | [40178378766](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40178378766) | All 13 jobs pass, including runtime images and matched aggregation |
| Optional Framework | [40178378700](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40178378700) | Pass; no production support activation |
| External Sandbox | [40178378759](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40178378759) | All four jobs pass |
| Security | [40178378721](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40178378721) | All seven locked environments pass |
| Docs | [40178378740](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/40178378740) | Pass |

Root passes 5,092 tests (3,266 explicit environment/profile skips), with 90.11%
coverage. ADK native/shared passes 1,456 (1,537 skips), LangGraph 1,529 (1,504
skips); each independently passes all 96 selected Portable Profile CTK cases.
Windows operation recovery passes 1,071 (475 skips), timers 744 (813 skips),
and each mandatory actual-SDK background matrix passes 13 without skips.
These skip counts do not qualify absent SDKs/backends: the independent native,
actual PostgreSQL and platform jobs supply that evidence. The four-cell matched
report and its ZIP digest are retained in the benchmark record. The local frozen
Docker counts and source hashes are recorded above. Earlier failures remain intact.

This closes the recorded finite scopes of S3-04 through S3-08, S4-01, S4-06
and S4-08. It does not promote all research features or proposed/schema rows,
activate conditional tracks, qualify the later optional history/JS/release-record
candidates, or publish a release. S3-03 still owns publication of its qualified
embedded background profile. Its deployment switch remains false by default.
The stale S2-09 JSON task status is also synchronized with its already checked
TODO/PROJECT and historical VM01-VM08 qualification at `9578459`; that repair
does not count as a ninth newly completed task.

The new publication checks exercise the real flag without a test override.
The first local Windows ADK selection has 12 passes and one producer-lease
failure: cold source attestation outlasted the fixture's five-second lease.
LangGraph passes all 13. The worker now prepares its real profile under a
separate 60-second setup bound before invocation creation; the original five-
second producer lease, 30-second recovery lease and operation deadline remain
unchanged. The corrected ADK receipt case passes. All earlier reports and the
sanitized ownership-failure artifact remain retained. This is a fixture correction,
not relaxed execution ownership or a claim that unknown outcomes are recoverable.

## Superseded backlog progress before d72781f qualification

The following original notes are retained as history; TODO.md contains the
current verified finite scopes and remaining tasks.

### S3-04 pre-qualification backlog text

```text
- [ ] **S3-04** (M) — Complete executable-operation portability through the existing SandboxManager and proven isolation profiles.
  JavaScript follow-up (2026-10-25): awaited Node 24 execution is authored on
  the common sandbox path, with physical interpreter recovery identity and
  pinned image components. Windows checks pass; Linux, image and exact-source
  qualification remain open. See the [candidate record](../s3-04-executable-source-acceptance.md#javascript-portability-candidate--2026-10-25).
  Captured sources, recovery proofs and awaited internal process controls are
  authored; only the dedicated expected source-policy rejection is translated
  into a user-facing policy error, while registry configuration/execution
  defects (including unrelated common unsupported-feature errors) propagate
  unchanged. Portable sandbox input paths now reject Windows drive, UNC and
  backslash-traversal forms, reserved Windows device names, alternate data
  streams, invalid characters and trailing-dot/space aliases before
  cross-platform consumption. New cases reject staged destination aliases after
  case folding, Unicode normalization or redundant separators. Stdio startup
  failures now log only a fixed OS error category while keeping the public error
  sanitized; its privacy regression awaits final-batch validation. Awaited
  child processes now bind `TEMP` and `TMP` to their execution workspace, and
  workflow attempts to override reserved runtime names (`PATH`, `HOME`,
  `TMPDIR`, `TEMP`, and `TMP`) are rejected case-insensitively; admission and
  actual-child regressions cover lowercase spellings and await final-batch
  validation. YAML admission and runtime setup now reject non-string sandbox
  environment names; their regressions await the consolidated validation.
  Stdio startup categories are logged only for process creation.
  Required
  profile and deployment acceptance remains
  open. See the
  [acceptance plan](../s3-04-executable-source-acceptance.md).
```

### S3-05 pre-qualification backlog text

```text
- [ ] **S3-05** (M) — Reuse durable protected approvals; add reassignment, escalation, waiting, denial/expiry and resume tests.
  Docker-backed PostgreSQL follow-up (2026-10-25) fixed two actor-test fixture
  collisions on populated databases using subject-bound operation keys and
  unique legacy IDs. Both repeat checks and the 195-case common PostgreSQL
  selection pass; production conflict/idempotency rules and task status stay
  unchanged. See the [Docker record](../s3-03-operation-adapters-acceptance.md#docker-and-postgresql-follow-up--2026-10-25).
  Protected actor identity, atomic decisions, expiry and native recovery are
  authored. The PostgreSQL timestamp migration and f9 database cases passed;
  its Windows timer lane exposed a request-row recovery race before the durable
  barrier. A five-second renewal lease and natural database-clock wait pass
  locally, and the formerly failing hosted Windows case passed at `8183776`.
  The new producer-stop witness replays reassignment/escalation after restart
  and checks the record/audit remain unchanged; its ADK/LangGraph × SQLite/
  PostgreSQL matrix and hosted requalification remain pending. The expired-
  approval fixture now decouples invocation ownership from its short approval
  deadline. The actual-process worker now replays approved, rejected and
  expired terminal decisions with their original actor, key, revision and
  value, then checks the complete durable approval snapshot remains unchanged;
  this is authored and awaits the final batch. Independent-SDK and hosted
  requalification remain pending. A new downgrade regression uses the
  independent protected-request index to prevent a corrupted common projection
  from exposing a protected approval through legacy GET or list APIs; it is
  authored and awaits the batch. Audit reconstruction now rejects non-positive
  or non-finite action timestamps and reassignment/escalation at or after the
  immutable request expiry, even when proof digests are coherently rehashed; a
  tampering regression awaits the same batch. The durable `max_pending` limit
  now rejects a new item when capacity is exactly full; a SQLite/PostgreSQL-
  parameterized regression verifies rejection and unchanged persisted state.
  At exact hosted source `5900c02`, PostgreSQL CI reported 1 failure, 1,987
  passed and 3 skipped: `test_protected_approval_queue_enforces_exact_pending_capacity[postgresql]`.
  The test fixture assumed an empty queue and request table; it now measures
  their pre-existing baselines and asserts capacity and persistence relative to
  those counts. The corrected fixture is authored and awaits consolidated
  revalidation. See the
  [acceptance plan](../s3-05-protected-approval-acceptance.md) and
  [recovery history](../validation-history/pending-batch-results.md#protected-approval-recovery-lease-boundary-at-f9cb76f).
```

### S3-06 pre-qualification backlog text

```text
- [ ] **S3-06** (L) — Complete identity propagation, credential references, policy/audit/isolation and authorization within existing contracts. Delegated token exchange and consent remain conditional S4-11 work.
  Unpaired Unicode in principal and named-profile references now fails as a
  bounded-reference `ValueError` instead of leaking an encoding exception;
  descriptor regressions cover identity, role, scope, audience and profile.
  External-catalog credentials are restricted to the normalized HTTPS origin
  (scheme, host and effective port), with focused resolver coverage for cross-
  port/scheme denial and default-port equivalence. Core identity/security
  regressions passed in the corrected consolidated local batch and documented
  focused selections. Full qualification remains open. Credential projection
  now extracts Bearer values regardless of authorization-scheme casing;
  lifecycle snapshot and SSE reads now pass through the configured
  `events.read` authorization gate before snapshot or stream access. A new
  native regression also asserts the actual model-credential canary is absent
  from captured logs alongside existing output, archive, receipt and lifecycle
  checks; this caplog assertion was authored after the consolidated run and
  remains unrun. Exact-source hosted and skipped backend qualification also
  remain pending.
  Foreign and missing schedule IDs now use the same fixed `ScheduleNotFound`
  response for reads and cancellation. A regression compares complete
  responses and verifies schedule records, owner/event scope, SQL rows,
  definitions, slots and model-call counts remain unchanged; its local
  regression is included in the consolidated batch where runnable.
```

### S3-07 pre-qualification backlog text

```text
- [ ] **S3-07** (M) — Finish all eight legacy agent patterns as bounded OWS workflows or explicit native extensions.
  Eight finite recipe families and named role bindings are authored; hosted
  acceptance remains open. A focused real-native role-policy test passes on
  SQLite for both principal engines, proving denial before provider dispatch;
  a new ADK/LangGraph × SQLite/PostgreSQL credential regression checks archived
  `from_env` references without a known environment canary. Its injected fake
  model does not resolve the credential. Recovery regressions also vary the
  model binding and tool allowlist and require rejection before provider or
  state mutation; a new recovery case also rejects a changed role authorization
  policy before provider or state mutation across the native engine/database
  matrix. Process-recovery drift cases also assert the exact
  `InvocationStateError` reason `runtime_profile_changed`. These additions are
  authored and await final-batch validation.
  PostgreSQL and the full eight-family matrix remain pending. See the
  [acceptance plan](../s3-07-agent-pattern-acceptance.md) and
  [timing evidence](../validation-history/pending-batch-results.md#windows-timing-suite-follow-up-at-8183776).
```

### S3-08 pre-qualification backlog text

```text
- [ ] **S3-08** (L) — Port selected ADK compiler/provider/broker/memory patterns and fixtures against canonical contracts.
  Selected finite adapters and grouped corrections are authored. A new
  cross-engine/backend regression checks direct LLM tool-request pass-through
  without dispatching an undeployed tool. Unpaired surrogate characters in
  provider tool IDs, function names or arguments now map to sanitized
  `ModelError`s. Escaped lone-surrogate argument and object-key cases now
  require sanitized `ModelError` handling; these regressions await consolidated
  validation. The function-name regression is also authored and awaits
  validation, while the other focused DTO regressions pass locally. Broader adapter/backend qualification remains
  pending. See the
  [acceptance plan](../s3-08-selected-adapter-acceptance.md).
```

### S4-01 pre-qualification backlog text

```text
- [ ] **S4-01** (M) — Implement versioned native YAML metadata extensions with capability admission; reject unsupported requests.
  The bounded declaration and exact engine-capability gate are authored in
  standard `document.metadata` without changing the official schema; focused
  core tests pass. S4-02's exact LangGraph channel extension is the first
  capability using this gate. A new regression withdraws the exact extension
  after an initial successful capability check and verifies a subsequent check
  rejects it; this regression passed in the consolidated local suite. Broader
  tracker coverage remains open. Adapter capability and native
  acceptance/rejection regressions are authored; engine-specific and exact-head
  hosted qualification remain pending.
```

### S4-06 pre-qualification backlog text

```text
- [ ] **S4-06** (M) — Run matched latency/CPU/memory/concurrency/persistence/recovery benchmarks; publish measured results.
  A separate matched-profile harness now records ADK/LangGraph × SQLite/
  PostgreSQL samples, nearest-rank p50/p95, invocation-phase CPU, Linux
  process-lifetime peak RSS, invocation-record creation latency, persistence
  footprint, an eight-invocation concurrent wave with p50/p95 and throughput,
  and timed engine and service reinitialization plus resume with stable
  operation-id and single-dispatch assertions. Concurrent settings are capped
  at 16 and the aggregate rejects missing or inconsistent latency summaries.
  PostgreSQL CI is wired to aggregate the four exact-source profile JSON files
  into one artifact. Harness tests and aggregate guards pass locally. The first hosted four-cell report passed at exact source
  `4b22e801f4f7f01d475caa03a754dd3ac45bf64a`; retained measurements are in the
  [benchmark record](../benchmarks.md#retained-exact-source-measurements) and
  [GitHub run artifact](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39459079488).
  On the later pushed source `0d812b2`, PostgreSQL CI and all four companion
  workflows passed, while CI failed in one Windows CPU-quota timing case and
  one Linux stdio child start; details and the current requalification are in
  the [validation history](../validation-history/pending-batch-results.md#hosted-outcome-for-source-0d812b2).
  Full qualification remains pending. Measurements document their runner and scope
  limits; PostgreSQL reports whole fresh service-database size, database I/O
  remains included in invocation latency rather than isolated by operation, and
  recovery reinitializes services in one process rather than forcing an OS stop.
  See the [S4-06 profile and limits](../benchmarks.md).
```

### S4-08 pre-qualification backlog text

```text
- [ ] **S4-08** (M) — Decide and document old openworkflow_adk import/CLI/package compatibility; port useful developer tooling.
  [The S4-08 decision](../s4-08-compatibility-decision.md) retains canonical
  metadata/import names, delivers through pinned source and qualified OCI
  images, disables Python index publication, and requires separate environments
  plus an explicit workflow port for consumers. No compatibility aliases or
  automatic checkpoint migration are offered. The machine-readable policy and
  real package/entry-point/publisher guards pass locally, as do four CLI cases
  including actual official-schema/compiler admission. `owa validate PATH`
  checks the default portable profile; target deployment settings still need
  their own admission. A future index publisher requires verified ownership,
  version and consumer evidence. Current-source full qualification remains open.
```
