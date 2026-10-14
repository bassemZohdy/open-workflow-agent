# Pending batch results — 2026-10-14

Historical attempts below remain distinct from qualification. S2-09's finite
VM01-VM08 policy is verified at PR head `9578459`; S3-01 through S3-08 remain
open. Passing subsets and fresh builds do not replace complete qualification.
Local `tmp/` evidence is ignored and retained in its originating worktree.

### Hosted checkpoint — PR head `9578459`

All six hosted workflows passed for PR #65 at exact head `957845969b39fbaa6698ff1cf150931c7947d2ba` (code correction `bfd149c`): [CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38559571167), [PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38559571023), [Agent Framework](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38559570870), [External Sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38559570878), [Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38559570925), and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38559570978). The root suite passed 4,562 tests, skipped 2,950, and reported 90.03% coverage against the 90% threshold. PostgreSQL passed all ten jobs. The Windows timer suite passed 718 tests and skipped 769 in 38m38s, inside the 45-minute job limit. ADK and LangGraph contract, CTK, lifecycle and recovery profiles passed; details are in the source-linked hosted reports. This verifies S2-09's finite VM01-VM08 continuation/recovery policy. It does not claim successful in-place v1→v2 migration, A086/T44, full native history, S3-01 integration, or any broader feature inventory row.

The preceding `bfd149c` workflow set was cancelled when the documentation checkpoint `9578459` was pushed; its result was superseded by the complete run above, not counted as a failure or a pass. The source changes in `bfd149c` reject undefined MCP configuration fields before authorization or I/O, but the focused tests do not qualify S3-01. Changes are now batched before the next hosted run to avoid restarting a running matrix.

### Hosted checkpoint — source `bfd149c`

The workflow run set was cancelled when a subsequent documentation checkpoint
was published before completion. Docs `38559048361` and Security `38559048345`
had passed; CI `38559048348`, Agent Framework `38559048460`, PostgreSQL
`38559048668`, and External Sandbox `38559048598` were cancelled or superseded.
These partial outcomes do not qualify any task. The local S2-09 recovery subset
passed 77 tests with 102 PostgreSQL/platform-specific skips. The MCP/protocol/tool
and workflow-selection batch passed 161 tests; this focused local evidence does
not qualify S3-01.

### Hosted checkpoint — source `c68950f`

Exact-source CI `38551999533` was cancelled because the Windows native timer
job reached its 35-minute limit during final pytest reporting. The job reported
686 passed and 674 skipped with no assertion failures before interruption;
fifteen of sixteen CI jobs passed, including root quality, ADK and LangGraph
native contract tests, and both Docker acceptances. The other successful CI
jobs covered deployment manifest validation, mutation testing, native
lifecycle/version recovery on both engines, Windows ownership/effect recovery,
checkpoint/routing, lifecycle/receiver recovery, jq admission, MCP
transport/auth controls and operation/process recovery. The actionable workflow
correction raises only the timer job's limit to 45 minutes. A fresh run is
required to qualify the source. PostgreSQL `38551999491`, Agent Framework
`38551999501`, Docs `38551999483`, Security `38551999498`, and External Sandbox
`38551999473` passed. Every PostgreSQL job passed, including common store, both
principal-engine persistence acceptances, native and lifecycle recovery,
operation-adapter recovery, and optional Agent Framework native recovery. No
task or feature is closed because the timer gate remains open.

Commit `c68950f` carries the Windows recovery fixture correction and bounded
failure diagnostics. The timer suite collected 1,360 cases: 686 passed and 674
were skipped under platform/feature gates. Its interruption occurred after
pytest logged those totals, during final reporting; no assertion failure was
reported. This is an actionable CI time-budget issue, so no task is deferred.
The exact Windows selector passed three consecutive local
repeats, and the related LangGraph process-recovery/A2A selection passed 14
tests with 14 PostgreSQL-only skips. This exact-source hosted run will determine
whether the correction qualifies; no result is inferred from the earlier run.

### Hosted checkpoint — source `605a908`

CI run `38547670098` completed with one failure. The Windows native lifecycle and
receiver-recovery job failed the LangGraph/SQLite partial-native
`test_sdk_async_ack_survives_real_stop_with_original_definition_and_clock`
case during `prepare_initial_delivery` with `InvocationStateConflict`. The
separate LangGraph native lifecycle/version-recovery job passed, as did the
root quality gate, Windows native timers, and ADK/LangGraph shared-contract
gates. All other CI jobs also passed. PostgreSQL run
`38547669853` passed.
External Sandbox `38547670158`, Agent Framework `38547669843`, Security
`38547669834`, and Docs `38547669859` also passed. The current source is not
qualified, and S2-09 and all feature rows remain open.

The focused batch on this source passed 85 tests with 34 PostgreSQL-only skips.
Ruff, format, strict typing, documentation links, implementation audit and
root/core builds passed. Its added cases cover 42 previously missed statements;
the hosted root quality gate has now confirmed the required coverage.
Repeated local runs of the exact Windows case passed admission with the
five-second lease, while the hosted failure occurred during admission. A local
15-second fixture lease passed admission. Recovery diagnostics then identified
an intermittent `python.architecture` difference: the restricted child
environment omitted the non-secret Windows architecture variables used by
`platform.machine()`. Commit `c68950f` preserves those variables, keeps
the 15-second lease, and emits only changed runtime-profile paths plus safe
traceback frames on failure. After this correction the exact Windows selector
passed three consecutive local repeats, and the related LangGraph
process-recovery/A2A selection passed 14 tests with 14 PostgreSQL-only skips.
The new exact-source hosted run is required before the correction or task can
qualify. No failure was deferred; the environment mismatch and admission timing
had actionable fixture corrections, published in `c68950f`.

| Frozen source | Profile | Recorded result | Meaning |
| --- | --- | --- | --- |
| `67aa724964edadb661f809bdde6949ce5af69b0f` | Hosted checkpoint | Aggregate failure; individual image/security/platform/database checks passed. | Draft PR checkpoint remains unqualified. |
| `f2220334c90e0d853fb348817fea5cbb3f08b218` | Root / ADK / LangGraph / optional Framework | 44 failures, 716 passes, 140 skips, 2 errors / 20 failures, 115 passes, 246 skips / 20 failures, 115 passes, 246 skips / 59 passes, 358 skips. | First operation batch; 979 inputs unchanged. |
| `9570557316014542fe10bfbd2361f70e2968df05` | Root / ADK / LangGraph / optional Framework | 22 failures, 755 passes, 140 skips / 10 failures, 260 passes, 295 skips / 6 failures, 264 passes, 295 skips / 3 failures, 251 passes, 429 skips. | Grouped correction batch; all four ended naturally with 980 inputs unchanged. |
| `6622ab08be0015ff143adb377a58709124d30c24` | Common executable-source and operation batch | 14 failures, 1,028 passes, 202 skips, 2 errors. | First executable-source common batch; 994 inputs unchanged. Principal executable-source profiles had not run at this source. |

Five independent package/provenance builds passed at each of these three local
operation/executable sources. They do not substitute for behavioral, native,
actual PostgreSQL, platform, image, security or publication gates.

Current corrections address retained wire/fixture mistakes, bounded input case
identifiers, actual source authority, stopped Windows process outcomes, gRPC
metadata and atomic leased completion. Fresh background-recovery diagnostics
identified synchronous profile verification delaying renewal of the original
five-second lease. A cooperative correction passed one actual LangGraph
producer-stop/recovery diagnostic with the unchanged lease, original input and
deadline, one business commit and distinct process identities. Broader regression
and hosted qualification remain pending; no timeout or acceptance threshold was
relaxed. A transient unknown supervisor observation remains historical; a separate
seven-second diagnostic is not qualification of the original shorter fixture.

Required next evidence and exclusions remain in [S3-03](../s3-03-operation-adapters-acceptance.md)
and [S3-04](../s3-04-executable-source-acceptance.md). Current completion and deferral
status remain in [PROJECT.md](../../PROJECT.md) and [TODO.md](../../TODO.md).

## Combined source, operations and approvals — `ebc2694`

The immutable local batch retains 1,016 tracked-input hashes. The common profile
ended with 23 failures, 1,089 passes and 227 skips in 528.37 pytest seconds
(545.19 seconds including driver/startup). Its end manifest records no tracked
source changes. All five fresh independent package build/provenance checks passed.
The optional Agent Framework profile finished with 254 passes and 565 skips in
581.03 pytest seconds; its end manifest records no tracked source changes.
Principal-only profiles skipped in this optional environment remain unqualified.
LangGraph finished with 30 failures, 274 passes and 397 skips in 1,165.04
pytest seconds (1,198.55 driver seconds), with no tracked source changes.
ADK finished with 30 failures, 274 passes and 397 skips in 1,344.40 pytest
seconds. Its end manifest records no tracked source changes. All four behavior
profiles have ended; the three failed profiles require a coherent correction batch.

The common failures identify five fixture calls to a nonexistent identity
projection, one fault fixture lacking required common error details, sixteen
protected-approval paths hashing tuple-based dataclass identity projections before
finite-JSON serialization, and one timer test assuming two renewals where one
unchanged five-second renewal already covered the seven-second workload. Source
corrections preserve strict JSON validation, actual original lease duration and
ownership checks. They are authored in the separate S3-06 checkout and remain
untested until that coherent batch is complete. This record qualifies no task.

The LangGraph failures also expose six reusable inline-script identity lookups
using a public reference where the private execution reference is needed; three
captured-script business-ledger assertions expose Windows Python stdin decoding
UTF-8 as the system code page, despite the returned stdout appearing correct;
and one source-proof corruption path acquires ownership before rejecting damaged
independent evidence. Protected approval native/process cases share the tuple
serialization issue and require review of producer ownership failures. These are
actionable correction work, not task deferrals. Original lease durations, captured
byte digests, public task references and fail-before-mutation gates remain required.

## Frozen S3-06 broad validation — dc71ecb

The combined correction/S3-06 source at `dc71ecb` freezes 1,034 tracked inputs.
All five fresh build/provenance checks passed. The broad common profile ended
with **46 failures, 2,684 passes, 591 skips and 59 setup errors** in 1,160.53 pytest
seconds (1,176.51 driver seconds), with no tracked input changes. The isolated ADK, LangGraph and optional Agent Framework profiles have also
ended; their final outcomes and bounds are recorded below.

Fifty setup errors came from a new fixture using occurrence zero where the
existing common effect identity requires a positive visit; two standalone journal
checks supplied an empty occurrence. Four API fixture errors lacked an explicit
fixture import. Repeated legacy component failures exposed an optional audit
participant treating unrelated standalone receipt history as native admission
history. The correction permits only default-disabled, unbound legacy services
with no current authority, candidate, original marker or independent authorization
proof; engine archive preflight and protected history remain fail closed.

Two PostgreSQL selector checks still expected 18 sandbox cases after five real
native return-projection cases were added (23 per engine). Other protocol/security
fixture failures have actionable fixes. Corrections and S3-07 source are authored
in a separate checkout; this failed frozen source is preserved. No completion or
CI qualification follows from these partial passes.

Source review of retained native failure evidence found a concrete engine filter
gap: independently verified original-authority receipts use the `/do` admission
scope, but both native adapters accepted only task references and the root clock.
LangGraph's initial SDK starter proof also retained a three-receipt bound while
valid authority admission can add a fourth internal receipt. Corrections must
allow only the exact original authority identity after common proof verification,
preserve all physical SDK starter checks, and continue rejecting business progress
or unrelated scope/policy records. These corrections remain source work until the
next frozen batch; those failed native runs are not retroactively qualified.

The remaining `dc71ecb` profiles have ended with unchanged tracked inputs.
Optional Agent Framework failed with 3 failures, 1,696 passes and 2,152 skips in
3,799.22 pytest seconds (3,831.87 driver seconds); the failures use a Windows
script fixture requesting an unsupported awaited file-size control. The runtime
rejection is preserved, and the positive fixture explicitly opts out of that
unavailable control on Windows in the next source batch. Both full principal SDK
profiles reached their original 4,500-second batch bound (exit 124), not a green
aggregate: ADK logged 1,266 passes, 33 failures and 1,422 skips; LangGraph logged
1,407 passes, 59 failures and 1,478 skips before termination. These are observed
progress counts, not final pytest totals or qualified test unions. Their artifacts
retain source hashes and full commands. Future isolated profiles exclude only
parameter instances belonging to the other unavailable SDKs; those instances run
in their own actual native environment, preserving shared fixture coverage.

## Frozen S3-07 and corrections — 3aaaf30

Commit `3aaaf30` freezes 1,054 tracked inputs. Static lint/format/type checks,
all 12 recipe schema/admission checks and all five fresh build/provenance checks
passed. The broad common run ended with **8 failures, 2,822 passes, 601 skips and
52 errors** in 1,037.85 pytest seconds (1,046.31 driver seconds), with no source
changes. Fifty errors were the new core role-boundary module requesting a fixture
scoped under the contract directory; two teardown errors released leases already
deliberately released by expiry/takeover tests. Remaining failures identify a
malformed-receipt exception escaping the common error contract, another legacy
unbound intent lookup, and an independent supervisor deadline observation gap.
The role fixture wiring, receipt translation, intent lookup and intentional stale
cleanup are corrected in the separate S3-08 checkout. Read-only verification of the retained HMAC observations found both original
deadline cases eventually terminal with the original deadline cause; the helper
returned an intermediate unknown observation too early. Healthy-terminal tests
now wait for terminal within the unchanged 20-second bound, while explicit
resource-failure cases still require unknown. The original seven-second execution
deadline and all production controls remain unchanged. Independent native
supervisor startup still needs diagnostic evidence; no security gate is weakened. Source authoring,
builds and partial passes qualify no unchecked task.

## Selected S3-08 adapters and corrections — 7214f46

Source `7214f4642280eae28e359423b627cd6265a400b8` freezes 1,067 tracked
inputs. Lint, formatting (526 files), strict typing (130 source files), all 12
recipe schemas and the unchanged 20-path public API schema pass. All five fresh
package builds and provenance checks pass. The full common profile stopped during
collection: `test_bounded_memory.py` imported nonexistent `ExecutionOwnership`
instead of the existing `ExecutionOwnershipStore`. Pytest reported one collection
error in 16.20 seconds; the driver ended with exit 2 after 27.72 seconds, with
no tracked source changes. No behavioral acceptance follows from collection.

Independent native profiles were stopped after observed failures once the grouped
corrections were ready: ADK and LangGraph each had 35 observed failures in named
role admission; optional Framework had one observed projection fixture failure.
These are progress observations, not final pytest totals. Explicit superseded
markers and result records preserve the source, command, stop reason and hashes.
No task checkbox changes.

## Corrected common fixture batch — caa32c8

The frozen common profile completed with 28 failures, 2,980 passes and 666 skips
in 1,145.42 pytest seconds (1,158.78 driver seconds), with unchanged 1,067 tracked
inputs. Failures are 25 role-boundary setup cases lacking immutable OpenAPI
binding fields, one nonexistent handle snapshot method, one older raw credential
option witness and one positive OpenAPI bearer fixture missing binding fields.
All have grouped fixture corrections authored separately; the actual initial
role archive admission defect is also corrected without loosening exact role
references. The common provider/composition modules recorded 80 passes, one
fixture failure and six actual PostgreSQL skips. New local memory and event
profiles passed their executed cases; required native, actual database and hosted
gates remain open. Source changes qualify no task or inventory row.

## Latest qualification source — 4740732

Frozen source `47407323776865704cda3bf5f48c6d9e5a91ea4e` retains 1,067
tracked-input hashes. The optional Agent Framework profile completed with
**767 passes, 354 skips and 2,934 deselections** in 1,637.99 pytest seconds
(1,661.19 driver seconds), exit 0. LangGraph completed with **73 failures,
1,146 passes, 672 skips and 1,467 deselections** in 3,865.85 pytest seconds
(3,895.09 driver seconds), exit 1. Neither completed end report records tracked
source changes or a timeout. Common and ADK were running when that initial
record was prepared; both subsequently reached the original 4,500-second batch
bound at logged 97% progress, exit 124. Common elapsed 4,501.184787511826 driver
seconds; ADK elapsed 4,501.303903102875 driver seconds. Both end reports record
`timed_out: true` and `source_changes: []`. No final pytest totals or qualified
union are inferred from their progress. All four profiles have ended.

The originating `qualification-fixes` worktree retains each command, source
manifest, log and end report under `tmp/adapter-batch-4740732-*`. The optional
Framework log SHA-256 is
`2b79d2ec1679f1b46147fb8340ea5c97d025e0b1e70c2c535bbe959fa4724a68`;
the LangGraph log SHA-256 is
`de9775018b5a37898d07d7a44524e461f0969aac1eccce5ff156d5e100ee4576`.
The bounded common log SHA-256 is
`1f02ae5243e5f60a7039110e4294409b71194ddd2513947ec0ee29d894a791fd`;
the bounded ADK log SHA-256 is
`eccf597cd5ff716ec9c23f4c2242e5ade4e0eedcc61bb2f72e728ff42444a8e0`.
Foreign native instances were deselected only in their independent environments;
actual PostgreSQL and other skipped gates are not inferred from local passes.

The separate `admission-renewal` checkout contains an authored bounded physical
artifact/SDK verification phase and five grouped fixture patches. Only copied
configuration and physical file verification move off-loop; database operations,
original trust checks and synchronous admission transactions stay on-loop.
Cancelled workers retain their bounded slots until completion. Fresh prepared
evidence belongs to one task and one synchronous phase, and changed provider or
deployment bindings reject before admission. The new source-level renewal witness
retains the original half-second lease and waits past its initial database-clock
expiry before validating renewed ownership; expiry/takeover and no-mutation
negatives remain required behavioral evidence.

Ruff and formatting pass across 527 files; strict typing passes across 134 source
files. Those were authoring-time checks before the correction source was frozen
at `883cd1a` and its full profiles launched, as recorded below. These
static results and authored witnesses do not retroactively qualify `4740732`,
complete any task, change feature statuses or substitute for remaining native,
database, platform, image, security and hosted publication gates. Earlier results
remain preserved in the sections above.

## Renewal correction batch launch — 883cd1a

Source `883cd1ae18662bb34f6deea76f41476c262c1f8b` freezes 1,068 tracked
inputs in `admission-renewal`. All five fresh independent build/provenance checks
passed before launch. At this source-time snapshot, four full profiles have
started and no final result reports are available. The common and ADK batch
supervisors use 5,400-second bounds (90 minutes); LangGraph and optional Agent
Framework retain 4,500-second bounds (75 minutes). These are batch supervision
bounds, not changed invocation leases, task deadlines or outcome assertions.

The originating worktree retains commands, selected environments, start records
and tracked-input hashes under `tmp/adapter-batch-883cd1a-*`. Final exit codes,
source-change records, log hashes and pytest totals must be added from the real
end reports after the profiles finish. This launch snapshot claims no behavioral,
backend, native, platform, security, image or hosted qualification and advances no
task checkbox or feature status. The preceding failed/bounded records remain
historical evidence, not qualified by the newer source or its passing builds.

## Frozen full profile batch — 0573ce7

Source `0573ce7535021162ef6051b10b52e9137f83aeb7` freezes the OpenAPI security
AND/OR correction, persisted supervisor-deadline recheck, and grouped A2A,
AsyncAPI, authorization, approval, executable-source, sandbox and service tests.
All four local profiles ran to completion at this exact HEAD with unchanged
tracked source during execution. Prior to launch, formatting, lint, strict typing,
lock checks, Docker Compose validation, and root/core package builds passed.

| Profile | Result | Duration | Exit |
| --- | --- | --- | --- |
| Root suite and coverage | 11 failed, 4,242 passed, 2,858 skipped, 59 warnings; 89.00% coverage (required 90%) | 5,416.30s (1:30:16) | 1 |
| ADK and shared/CTK | 3 failed, 1,401 passed, 1,404 skipped, 2 warnings | 6,141.70s (1:42:21) | 1 |
| LangGraph and shared/CTK | 3 failed, 1,455 passed, 1,377 skipped, 1 warning | 5,696.08s (1:34:56) | 1 |
| Optional Agent Framework and shared/CTK | 1,699 passed, 2,356 skipped, 1 deprecation warning | 5,087.80s (1:24:47) | 0 |

The root coverage denominator was 24,288 statements with 2,671 missed. Root
failures include short schedule/cancellation polling, a CI selector count made
stale by the new finite approval cases, an AsyncAPI preservation fixture using
an unsupported schema keyword, and assertion mismatches for the accepted common
corruption error, sandbox control directory, and approval API response. The
SQLite damage cases remain fail-closed and made no dispatch. The remaining root
failures are traceable to those test expectation/polling corrections and the
coverage deficit.

ADK and LangGraph both reproduced the one-second schedule polling timeout. ADK
also timed out waiting for a shared eventing state under full host load and its
deliberate expired-approval subprocess surfaced an `OperationalError` before
producing recovery evidence. LangGraph exposed a transient Windows
`PermissionError` while its test read the atomically published ready marker, and
an ordinary protected-approval case lost its intentionally short 0.5-second
test lease. These profiles do not establish engine acceptance.

The four retained logs are local ignored artifacts under
`tmp/qualification-0573ce7/`:

| Log | SHA-256 |
| --- | --- |
| `root.log` | `27f39cf776a0a62151e7b6f6bfa35079c156b6bde7a8a79cf1d64514f90ccf8e` |
| `adk.log` | `0999ae4b4c0bbfd2af700795510db3cb26f29c42d9e7530ac5458ac7844add87` |
| `langgraph.log` | `df9eb9ab37dcd2fba10f993bda2365b4d3feda3c7d604e36c192f765509f93c1` |
| `framework.log` | `3d20334555022e8f722965067ecd77b39abbaef81f050bc66269e71fbed1e2de` |

This is a failed local qualification record, not hosted CI evidence. No pending
task, feature row, backend, JavaScript profile, or release advances from these
results. The follow-up batch addresses the observed fixture issues and adds
behavioral coverage without lowering the 90% threshold; a fresh full run is
required before any acceptance claim.

## Frozen full profile batch — 61844c4

Source `61844c4c71f2eb27cadcec2b206fe557d75b8bce` freezes the grouped
corrections and added deterministic core coverage. Before launch, repository
Ruff, formatting, strict mypy, lock checks, Compose validation, and root/core
package builds passed. Four behavioral profiles ran against this exact source;
the tracked worktree remained unchanged.

| Profile | Result | Duration | Exit |
| --- | --- | --- | --- |
| Root suite and coverage | 12 failed, 4,329 passed, 2,923 skipped, 59 warnings; 89.34% coverage (required 90%) | 6,110.33s (1:41:50) | 1 |
| ADK and shared/CTK | Five failures observed; pytest reached all cases but stalled after the warnings at the short-summary heading. Exact passed/skipped totals were not emitted. | 8,963.63s (2:29:24) | -1 (runner stopped after idle final-report stall) |
| LangGraph and shared/CTK | 6 failed, 1,679 passed, 1,673 skipped, 19 warnings | 7,727.37s (2:08:47) | 1 |
| Optional Agent Framework and shared/CTK | 1 failed, 1,698 passed, 2,356 skipped, 1 warning | 5,744.49s (1:35:44) | 1 |

Root coverage measured 24,288 statements with 2,588 missed. The root failures
were one A2A streaming SDK fixture, one SQLite corruption expectation, two
internal-sandbox supervisor expectations, one protected-approval response
assertion, five recovery-binding fixtures that failed before their intended
declaration checks, one unsafe-JSON message assertion, and one missing
transition-target fixture. The failure names and full traces are in the root
log.

ADK emitted five failures: four process-recovery cases reported unavailable or
inconsistent original-definition evidence (including executable-source,
external-receiver, deadline, and OpenAPI recovery), while expired protected
approval recovery surfaced an `OperationalError`. Its log also ended with a
gRPC server destructor warning after the event loop had closed and an unawaited
`AioServer.shutdown` coroutine. The pytest worker remained idle with no log
updates for more than six minutes after all cases had completed, so it was
stopped; the final summary totals and a normal pytest exit were unavailable.

LangGraph's five process-recovery failures involved detached-child admission,
execution authority, external receiver, gRPC, and OpenAPI restart scenarios;
protected-approval race recovery returned a workflow error instead of the
expected conflict. The optional Agent Framework profile failed the 5-second
scoped-fork cancellation case while asynchronous recovery work consumed the
window. These symptoms require targeted diagnosis and do not establish a
qualified profile. Root coverage remains below threshold; do not lower it.

The four local logs are ignored artifacts under `tmp/qualification-61844c4/`:

| Log | SHA-256 |
| --- | --- |
| `root.log` | `59ff9aa0ef3d6a370bd0429345869cb4c698b3ec4ec396b817d93c5115ee214d` |
| `adk.log` | `6a6b9f2aec36336bac815b36baeef88582c1e93e1e82f91a4766274e9d611f05` |
| `langgraph.log` | `85f2b6fc81a6da926f992913d70f7c40ad33997dbf868179f150f4622898398a` |
| `framework.log` | `c6eef34919ad0229ff380423d74809740e2a78f1fafb396c1096c7e740a638c9` |

This batch failed local qualification and provides no hosted CI evidence. No
task, feature, backend, JavaScript profile or release advances from these
results. Correct the fixtures and diagnose the fail-closed recovery errors and
runner cleanup before the next full batch. The prior `0573ce7` and `883cd1a`
records remain historical, as do their outcomes.

Subsequent focused diagnostics in the correction worktree passed all six
previously failing LangGraph recovery cases twice (6 passed, 186 deselected in
131.01s and 139.33s) and all five observed ADK recovery cases (four subprocess
cases: 4 passed, 152 deselected in 138.32s; workflow-deadline case: 1 passed,
67 deselected in 11.07s). The ADK receipt-consistency case implicated by the
suite cleanup warning also passed alone (1 passed, 95 deselected in 12.55s),
without that warning. Test-only failure diagnostics passed two secrecy/metadata
tests. These isolated results make host contention or suite interaction the
current explanation to investigate; they do not replace full-profile or hosted
qualification and did not justify changing production recovery checks.

## Corrective source batch — hosted result and active follow-up

The correction batch based on `61844c4` passed its consolidated focused
regression selection: 377 passed and 176 skipped in 194.56s. Ruff lint and
formatting, strict mypy for CI and all runtime packages, all seven lock checks,
embedded/PostgreSQL Compose validation, Markdown relative links, and root/core
wheel and source builds passed. The measured merged statement union is
21,860/24,288 (90.0033%); the CI gate now sets `--cov-precision=2` so rounding
cannot hide a below-90% result.

The full root command was also started on the local Windows workstation, but
was deliberately stopped after 45 minutes at 33% while running the contract
suite. That run produced no pytest summary and is incomplete, not a pass or a
test failure.

### Hosted validation of corrective batch — PR 65

The exact-source hosted run for [PR #65](https://github.com/bassemZohdy/open-workflow-agent/pull/65)
used source `a4aac1b9e0ab9fc4c07a83d7259da818c572b8bb`. Documentation, security,
deployment manifests, both Docker acceptance jobs, ADK/LangGraph lifecycle
recovery, and several native recovery jobs passed. PostgreSQL common-store and
both PostgreSQL operation-adapter jobs failed in protected-approval paths. The
approval table declared expiry timestamps as PostgreSQL `REAL` (32-bit), while
the protected record retains the exact timestamp; rounding caused the exact
consistency proof to fail. PostgreSQL common-store also found a stale sandbox
delivery row: a corruption fixture deleted its admission row, then teardown
used that deleted row to discover which delivery row to remove. Narrow fixes
address both findings.

The Windows jq/admission job found two timing assumptions. A 40 ms memory
retention window could expire entries during sequential setup, and a one-second
imported-call timeout could expire before the LangGraph call was dispatched.
The test fixtures now use a controlled clock and a longer in-flight timeout.
Root, ADK, LangGraph, and remaining Windows jobs had not all finished when this
checkpoint was recorded. These results do not qualify S2-09 or any other open
task. A fresh exact-source hosted run must pass root coverage, both principal
engines, optional Agent Framework, Windows, PostgreSQL, containers, security,
and documentation. The strict 90% threshold and acceptance gates remain
unchanged.

### Completed hosted results for source `a4aac1b`

The remainder of CI run `38522215454` and PostgreSQL run `38522215528` completed
after the checkpoint above. Root quality ended at 4,436 passed and 2,929
skipped, but failed the unchanged coverage gate at 89.54% (24,289 statements,
2,540 missed). Its cancellation/descendant test also failed before cancellation
because its child-process marker was not written. The ADK and LangGraph native
and contract lanes failed their real-process tests because the uv-managed
CPython build did not expose Linux `os.pidfd_open`; the tests correctly require
a stable OS process handle and fail before making a recovery claim. Windows
native timer/event/scheduling had one timing-sensitive workflow timeout result
that arrived before the test observed the intended unresolved action. The
remaining Windows lanes and the optional Agent Framework lane passed.

PostgreSQL common-store and both operation-adapter jobs failed in the protected
approval and sandbox-fixture cases described above; native recovery and
lifecycle jobs passed. The follow-up retains the strict handle checks, raises
the descendant-only process limit while preserving all other sandbox limits,
uses the hosted CPython interpreter for Linux pidfd lanes with a preflight, and
allows the in-flight retry-timeout fixture enough time to reach its durable
boundary. New A2A rejection and terminal-stream cases add behavioral coverage.
These changes are being validated as one follow-up batch; no task status or
acceptance gate advances on this failed source checkpoint.

### Provisional hosted checkpoint — source `a6c2faa`

As of the latest status check, CI run `38528004082` and PostgreSQL run
`38528003740` were not clean. External-sandbox run `38528003908`, Agent
Framework run `38528003725`, documentation-link run `38528003873`, and security
run `38528003816` passed. The PostgreSQL run failed both principal-engine
operation-adapter jobs. LangGraph job `115733371623` had three failures: the
losing protected-approval race returned a generic proof error instead of
`ApprovalConflict`, and the rejected and expired approval-pattern cases each
consumed an approved action. ADK job `115733371729` failed the rejected and
expired approval-pattern cases because the model also consumed approved
actions. A rerun of the LangGraph PostgreSQL job was accepted; the ADK rerun
request was rejected with HTTP 403 because its parent PostgreSQL workflow was
still running.

CI job `115733373016` failed a Windows operation-recovery case for a cancelled
LangGraph parent-stop scenario: the child startup assertion failed, and the
available report did not include a usable stack trace. At this checkpoint,
root quality, Windows timer tests, and both ADK and LangGraph contract lanes in
CI run `38528004082` were still running. These are provisional results, not a
complete run. No task, feature, or acceptance gate advances on `a6c2faa`; record
the eventual run completion and any corrective source separately.

### Final status snapshot for source `a6c2faa` — 2026-10-14

The original PostgreSQL LangGraph operation-adapter attempt failed, and its
rerun completed with 3 failures, 218 passes, 158 skips and 379 deselections:
the approval race and both stale-event pattern cases. The latest PostgreSQL ADK
operation-adapter attempt failed both stale-event pattern cases. PostgreSQL
common-store, native recovery and lifecycle jobs passed.
CI root quality finished 4,453 tests with 2,931 skips but failed the unchanged
90% core coverage gate at 89.54%. The ADK contract job reached 94% progress
before its 1,200-second timeout. LangGraph contract tests passed; Windows timer
tests failed the protected-approval race case. The Windows operation-recovery
job remains failed on the cancelled-parent child-startup assertion. The
successful external-sandbox, Agent Framework, documentation-link, and security
runs are unchanged. Follow-up changes subsequently committed in `dcc16a6`
correct retained approval-event matching, isolate approval-race contenders on
separate database connections, add ownerless authenticated-recovery and native
MCP/mTLS regression cases, and capture scrubbed Windows worker stack frames.
The focused root/MCP/recovery
diagnostic tests passed (104 passed); both-engine pattern checks passed (2 per
engine), the isolated LangGraph race passed three consecutive runs, and the
native empty-MCP-result receipt case passed on each engine. Static checks and
root/core builds passed after a formatting correction. This snapshot records
the final known status for `a6c2faa`; its local follow-up changes were later
committed in `dcc16a6` and require that source's own hosted validation.

### Hosted checkpoint — source `dcc16a6`

At this historical checkpoint, CI run `38533361107` and PostgreSQL run
`38533361022` were the active qualification runs. External-sandbox run
`38533361035` (contract, Docker and Kubernetes acceptance), security run
`38533361011`, Docs run `38533360976`, and
optional Agent Framework run `38533360960` passed. In CI, both Docker
acceptance jobs, deployment manifest validation, mutation testing, Windows MCP
transport/authentication, Windows task checkpoints/routing, Windows ownership
and effect recovery, Windows operation adapters/process recovery, Windows jq
admission, LangGraph native/contract tests, and both engines' native
lifecycle/version recovery passed. Windows native lifecycle/receiver recovery
failed one A2A initial-delivery case; Windows timers, root quality and the ADK
native/contract lane were still running at this snapshot. PostgreSQL common
store, lifecycle, both native recovery jobs, Agent Framework recovery and ADK
operation-adapter recovery passed. The PostgreSQL LangGraph operation-adapter
job failed one process-recovery case (220 passed, 158 skipped, 379 deselected);
the ADK native recovery job subsequently passed. The A2A start failure did not
reproduce in repeated local runs, and a subsequent local run skipped because
the native platform prerequisites were unavailable. Safe start-stage and
traceback-frame diagnostics have been added locally for a rerun; they are not
part of `dcc16a6`. These failures have actionable diagnostic steps and remain
active, not deferred. Partial outcomes do not advance any task. Record final
outcomes and corrective source in a dated follow-up after the remaining CI
jobs settle.

The diagnostic source `7405371` was pushed before the pending CI root-quality
and Windows timer jobs completed; GitHub canceled those jobs when it
superseded the `dcc16a6` commit. No completion result is inferred from that
run.

### Hosted checkpoint — source `7405371`

At that checkpoint, the diagnostic follow-up was being validated by CI run
`38536681297` (queued),
PostgreSQL run `38536681053` (in progress), External Sandbox run `38536681041`
(in progress), and Agent Framework run `38536681032` (in progress). Security
run `38536681078` and Docs run `38536681077` passed. This source adds
secret-safe start-stage A2A admission details and traceback frames for the
PostgreSQL selected-adapter recovery worker. These results are partial and do
not advance any task; record the completed outcomes and any corrective source
in a dated follow-up after the runs settle.

### Hosted checkpoint — source `523522b`

CI run `38536987531` failed two gates. Root quality completed 4,457 tests with
2,935 skips and 62 warnings; coverage measured 89.54% against the required
90%. The
Windows native timers/events/scheduling lane passed 701 tests, skipped 754 and
failed the protected-approval race case: the losing decision raised
`WorkflowExecutionError` instead of `ApprovalConflict`. All other CI jobs passed.
PostgreSQL run `38536987632`, External Sandbox run `38536987569`, Agent Framework
run `38536987509`, Security run `38536987580`, and Docs run `38536987493` passed.
This source remains unqualified; no task or feature advances.

Source `4d2e224` routes actor-bearing protected decisions through a single-row
request lookup into the existing proof-checked transaction, avoiding a
multi-query preflight that could observe mixed SQLite state. Focused tests cover
that route and its authorization fallbacks, recovery evidence, event retention,
A2A stream edge cases and previously uncovered core branches. Locally, the
combined focused selection passed 133 tests with 23 PostgreSQL-only skips;
lint, format, strict typing, documentation links, the implementation audit,
and root/core builds passed. Hosted qualification is in progress; partial
results do not advance any task.

### Hosted checkpoint: source 4d2e224

PostgreSQL run `38542590348`, Agent Framework run `38542590274`, External
Sandbox run `38542590247`, Security run `38542590300`, and Docs run `38542590324`
passed. CI run `38542590245` failed. Root quality completed 4,527 tests with
2,939 skips and 62 warnings, but coverage was 89.90% against the 90% gate and
the finite approval test-grid witness expected 36 nodes instead of 45. The ADK
native/shared contract step reached 95% but hit its 30-minute command limit
(exit 124); pytest printed the next synchronous-child case at the same
timestamp, so the evidence does not identify a hanging test. The Windows
native-timer job was cancelled after CI failed. Every other CI job passed. A
prior hosted checkpoint ran the same 2,810-item ADK selection successfully in
1,423.97 seconds. No task or feature advances on this source.

The corrective batch was published as `605a908`. It updates the protected-
approval selector to 67 nodes, raises the ADK test-command budget from 30 to 40
minutes within the existing 45-minute job, and adds focused fail-closed coverage
for approval request proofs, immutable recovery definitions, A2A streams and
initial delivery dispatch. Its local combined focused selection passed 85 tests
with 34 PostgreSQL-only skips. Added cases cover 42 previously missed
statements; the projected root coverage is about 90.07%, subject to confirmation
by the full hosted gate. Ruff, format, strict mypy, docs-link and
implementation-audit checks, and root/core builds passed. The incomplete hosted
outcome is recorded above. S2-09 and all feature rows stay open.
