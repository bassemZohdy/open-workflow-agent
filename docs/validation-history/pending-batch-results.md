# Pending batch results — 2026-10-17

Historical attempts below remain distinct from qualification. S2-09's finite
VM01-VM08 policy and S3-01's finite Python MCP01-MCP08 profile are verified.
S3-02 through S3-08 remain open. The final S3-01 exact-source result is recorded
first; earlier attempts remain historical and superseded. Passing subsets and
fresh builds do not replace complete qualification. Local `tmp/` evidence is
ignored and retained in its originating worktree.

### Exact-source qualification — `af4acd6`

All six hosted workflows passed at exact source
`af4acd61c200787fb68248b2d180ba69efb8c837` for PR #65: [CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38586728177),
[PostgreSQL CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38586728590),
[Agent Framework CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38586728663),
[External Sandbox CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38586728346),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38586728258), and
[Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38586728332).
The root quality suite passed 4,569 tests, skipped 2,950 and reached 90.04%
coverage. The Windows operation-adapter/supervised-process recovery suite passed
962 tests and skipped 454; the Windows timer suite passed 718 and skipped 769.
The Windows common MCP/process-lifetime profile passed 389 tests (43 skipped).
PostgreSQL passed all ten jobs, including principal-engine MCP lifecycle and
recovery selections. The two principal-engine lifecycle/recovery selections
each passed 125 tests (87 skipped, 219 deselected), and their operation-adapter
selections each passed 221 (158 skipped, 379 deselected). Agent Framework,
External Sandbox, Security and Docs workflows also passed.

This qualifies S3-01 only for the finite Python MCP01-MCP08 profile. The tracker
advances only A142-A144 and A146-A152 as `qualified_subset`; A145 remains
partial. JavaScript is unimplemented, `full_feature_verified` remains false,
and proposed T29/T30/T31/T67 have not been executed. The six results do not
claim full MCP protocol conformance or broader feature completion. S3-02 is now
the next open task. This checkpoint supersedes the incomplete `17501ee` and
diagnostic follow-ups below for finite S3-01 acceptance.

### Superseded Windows recovery diagnostic follow-ups

Source `0d98ee9832499df2ab843ab9d51644b0b36d2267` increased the released-job
observation wait and added value-free diagnostic context. Docs and Security
passed (`38585099998`, `38585099909`); CI, PostgreSQL, Agent Framework and
External Sandbox were cancelled or superseded before completion. Review then
found that the parent process could time out before the worker wrote its
diagnostic artifact. Source `d45450cdeffb706e33b4db1bc0789f72e924d649` raised
that parent result wait to 150 seconds. Docs and Security passed
(`38585947748`, `38585947568`); CI (`38585948374`), PostgreSQL
(`38585947979`), Agent Framework (`38585948315`) and External Sandbox
(`38585947443`) were cancelled while batching the remaining documentation
cleanup. These runs are partial evidence only and qualify no task or feature.
This diagnostic source was followed by the complete green exact-source matrix
recorded above. The local Windows recovery selector could not start its
supervisor in this environment; hosted Windows CI supplied the authoritative
validation.

### Exact-source validation attempt — `17501ee`

Source `17501ee3b09c7d3ff9c954cc19125497f3c15130` completed all six hosted
workflows for PR #65: [CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38574392068),
[PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38574392069),
[Agent Framework](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38574392078),
[External Sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38574392061),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38574392054), and
[Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38574392122).
PostgreSQL, Agent Framework, External Sandbox, Security and Docs passed. CI's
root quality gate, engine suites, deployment checks, Docker acceptances and
other Windows jobs passed. The root suite passed 4,569 tests, skipped 2,950 and
reported 90.04% coverage. PostgreSQL passed all ten jobs. The Windows timer
suite passed 718 tests and skipped 769 in 38m33s. The Windows operation-adapter
job had one failure among 962 executed cases: the completed LangGraph
independent-job recovery boundary timed out after 25 seconds waiting for its
released background job to become terminal (1 failed, 961 passed, 454 skipped,
one warning in 24m40s). Its receipt, cancellation and drain LangGraph sibling
cases passed. The failure artifact contained only the sanitized assertion
frame, so it did not retain job or supervisor state. This is actionable;
At source `17501ee`, S3-01 remained open and no task or feature row advanced. The follow-up attempts
and next required full matrix are recorded above.

### Superseded diagnostic pass — `b0bd076`

Source `b0bd076762e5ee99265f2aeb96c84c8477a17984` added output-shape diagnostics
for the `b3d7b61` PostgreSQL recovery failure. During the new source run, review
found that serializing arbitrary output-map keys could include data in the
diagnostic. The CI, PostgreSQL, Agent Framework and External Sandbox runs were
cancelled before completion; Docs and Security completed successfully but do
not qualify the source. The diagnostic now reports only fixed key-presence
flags, types, collection length and equality booleans. No task or capability
advanced from this interrupted pass.

### Exact-source validation attempt — `b3d7b61`

Source `b3d7b613c723df704e30db18009fcb615a5a4a22` completed the six hosted
workflows for PR #65: [CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38566833826),
[PostgreSQL](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38566833665),
[Agent Framework](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38566833871),
[External Sandbox](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38566833600),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38566833673), and
[Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38566833802).
Five workflows passed. The root suite passed 4,569 tests, skipped 2,950, and
reported 90.05% coverage. The Windows timer suite passed 718 tests and skipped
769 in 32m50s. PostgreSQL passed seven jobs; its LangGraph operation-adapter
recovery job failed one case (`inbox-before-ack`) after 220 passed, 158 skipped
and 379 deselected. Recovery completed, but its output differed from the
expected value; that source's worker did not record a safe description of the
differing shape. The other PostgreSQL jobs passed; the persistence matrix job
was skipped by its configured selector. This is not a completed qualification
checkpoint. At this source, S3-01 remained open until a corrected source passed its complete
hosted matrix; no capability or task is advanced by this attempt.

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

### Follow-up at `4014874`

The documentation-only source `4014874629cf1200cadb0e0dc8b474d8002a16c0`
passed Security (`38595583530`), External Sandbox (`38595583533`), Agent
Framework (`38595583607`) and Docs (`38595583540`). Fifteen of the sixteen CI
jobs passed; the Windows native timer/events/scheduling job (`115961332862`)
was canceled at its 45-minute job limit, so CI run `38595583529` is incomplete.
PostgreSQL CI run `38595583614` failed one test in the LangGraph operation
adapter recovery job:
`test_real_stop_between_common_admission_ack_or_native_projection_keeps_original_event[inbox-before-ack-postgresql-langgraph-LangGraphWorkflowEngine]`
(220 passed, 158 skipped, 379 deselected). The bounded diagnostic on that
source reported that the completed workflow result was a list rather than the
expected event/model mapping, without exposing its contents. ADK and all other
PostgreSQL jobs passed. This is an actionable S3-03 recovery discrepancy, not
a reason to defer it or to advance S3-02.

The local Windows reproduction used PostgreSQL 16 and the same selected
LangGraph case. It did not reach recovery: the producer failed to reach the
expected `waiting` state within its 40-second start bound. The failure artifact
contains only the sanitized worker frames, so this attempt neither explains nor
reproduces the hosted output-shape mismatch. The recovery worker records
value-free shapes at native execution return and common result/persistence
readback. No task is deferred.

### Hosted recovery rerun at `bbe9625`

PostgreSQL CI run `38602669648` passed all ten jobs. In particular, LangGraph
operation adapter recovery job `115985570066` passed 221 tests, skipped 158 and
deselected 379; the previously failing
`test_real_stop_between_common_admission_ack_or_native_projection_keeps_original_event[inbox-before-ack-postgresql-langgraph-LangGraphWorkflowEngine]`
parameter passed. The hosted mismatch did not recur, so these results do not
explain or resolve its cause. Keep S3-03 open and retain the bounded diagnostics
to identify the first layer where the event mapping changes if it recurs.

The same CI run's Windows native task-checkpoint job (`115985569940`) failed
`test_real_stop_between_common_admission_ack_or_native_projection_keeps_original_event[outbox-sqlite-langgraph-LangGraphWorkflowEngine]`
with `InvocationStateConflict` during recovery. The test worker had shortened
the production 30-second execution lease to five seconds and waited a fixed
15 seconds for expiry. The fixture now leaves the production lease unchanged
and derives its expiry wait from that lease plus ten seconds. The local Windows
LangGraph and ADK SQLite selections then each passed four cases and skipped
twelve backend/engine-inapplicable cases. Hosted requalification on the
corrected fixture remains pending; this does not change runtime ownership
semantics.

The same documentation follow-up records finite A2A test evidence at the prior
exact runtime source `af4acd61c200787fb68248b2d180ba69efb8c837`; A2A02 is directly
evidenced for its bounded Python profile, while A2A01 and A2A03-A2A08 remain
partial. The A2A record maps each gate and exclusions. The current 401 head adds
no runtime change and does not upgrade or invalidate that A2A evidence.

### A2A08 witness trace at `fedbdee`

The acceptance record and implementation audit now cite the bounded stream
timeout, terminal-snapshot close, event/backpressure limits, cross-worker final
commit observation, outbound SDK stream identity/completion and disconnect
cases by exact test symbol. The focused selection passed 29 cases locally on
the Windows checkout; audit/tracker checks passed six tests and Markdown links
validated. This reconciles individual witnesses only: durable task/receipt
expiry, lost-response, malformed-event and required backend/binding cross-matrix
remain open, so S3-02 and A2A08 stay partial.

### A2A08 accepted-send timeout

An additional actual-TCP SDK witness stalls the peer only after its streaming
message handler accepts the business call. The client reports a bounded timeout
as uncertain and records exactly one network request and one business dispatch;
the focused Windows run passed. This covers loss before the first stream frame,
not reconnect policy or durable receipt expiry.

The official A2A SDK's actual-TCP `SendStreamingMessage` path also received a
deliberate connection close after its first SSE event. The client returned the
uncertain outcome without reconnecting, and the fixture recorded exactly one
request. This proves partial-response loss handling for that method only;
reconnect and durable receipt expiry remain open.

### A2A07 actual TLS and named credentials

The official A2A SDK made loopback HTTPS `SendMessage` calls using deployment-
named bearer, API-key, OAuth and mTLS profiles. The OAuth client completed its
token exchange over HTTPS with the fixture CA, selected the endpoint in the
fetched AgentCard, and used the issued bearer token on the A2A request without
inheriting that credential during card discovery. Bearer and API-key profiles
also reached the HTTPS AgentCard-selected peer through their exact grants; the
discovery request carried neither business header. The mTLS peer required a
certificate trusted by its test CA; the call completed, while a second call
without a client certificate did not reach business dispatch. The bearer and
API-key requests carried only their selected credential headers to the TLS
peer. The focused Windows tests passed. The full card-selected origin/resource
policy and backend matrix remain partial.

The first bearer/API-key fixture draft incorrectly attached a CA-bundle field
to credential profiles that do not define one; both parameter cases failed
profile validation before making a request. The fixture now inherits the CA
from the configured HTTP client and the named credential from that same
client. Both actual-TLS cases then passed, along with the required-client-cert
mTLS and TLS OAuth cases.

The first AgentCard-linked case draft passed the already-normalized card mapping
to the protobuf JSON converter and failed before business dispatch. Passing the
validated mapping directly into the call then passed the actual-TLS OAuth,
card-selection and credential-isolation case.

At exact source `b11e2dd979aef8d6486621748066658557e257dd`, the grouped
Windows-local SDK file passed 97 tests after the partial-stream and four TLS
credential witnesses were added. The implementation-audit and
consolidation-tracker checks passed six tests; Markdown link validation, Ruff,
format checking and `git diff --check` also passed. HTTPX emitted six CA-path
deprecation warnings from the current path-based TLS configuration; these did
not fail the tests and do not change the acceptance result.

At exact source `243b62222968461b2127f653d56f9c216f9c3a67`, the same 97-test
SDK file passed with OAuth bound through actual HTTPS AgentCard discovery to
the selected peer; audit/tracker, documentation-link, Ruff, formatting and diff
checks also passed. The updated path-based TLS tests emitted seven HTTPX CA-path
deprecation warnings.

At exact source `a60f6db3c551942133fcbed8399a0d1727b6de3f`, the 97-test SDK file
passed with bearer and API-key credentials also bound to the fetched
AgentCard-selected HTTPS peer; the discovery request carried neither credential
header. Fifteen workflow/audit/tracker tests, Markdown link validation, Ruff,
formatting and diff checks passed. The TLS cases emitted nine HTTPX CA-path
deprecation warnings.

### Hosted requalification at `fedbdee`

Docs, Security, External Sandbox, Agent Framework and all but one main CI job
passed. Main CI run `38610573315` failed ADK native lifecycle job
`116012542050` when its shell-level eight-minute timeout exited 124 at 94%
progress while the queue-corruption recovery case was running. No assertion
failure was reported. The same ADK/SQLite queue-corruption parameter passed
locally on Windows (one passed, three backend/engine skips; 28.58 seconds).
The suite's command budget is raised to ten minutes; hosted requalification is
still required.

PostgreSQL run `38610573562` initially failed the LangGraph operation-adapter
recovery parameter
`test_real_stop_between_common_admission_ack_or_native_projection_keeps_original_event[inbox-before-ack-postgresql-langgraph-LangGraphWorkflowEngine]`.
The bounded result, stored value and native-execute diagnostics each had the
shape `[None]` instead of the expected event/model mapping. Rerun job
`116024883750` passed the same parameter; all ten PostgreSQL jobs passed, with
221 passed, 158 skipped and 379 deselected in the LangGraph adapter-recovery
job. The output-shape mismatch is still unexplained and remains an actionable
S3-03 diagnostic, not a deferred task. The corrected Windows lease fixture also
passed its hosted ownership/effect recovery job on this checkpoint.

### Hosted Windows adapter timeout at `f830fa6`

The f830fa6 main CI run `38617277541` completed every job except
`Windows operation adapters and supervised process recovery`
(`116035485480`), which was cancelled at its 35-minute job limit. Its pytest
summary at 98% was 952 passed, 446 skipped and one failure after 34:34. The
failure was
`tests/core/test_sandbox_jobs.py::test_original_deadline_proof_keeps_first_control_and_truthful_terminal_cause[sqlite-None]`:
the test admitted a job with a 0.5-second deadline and real setup elapsed past
that deadline before `begin_submit`. This was a test-clock race under the long
Windows suite, not a job-store behavior change. The proof now uses a controlled
store clock to advance beyond the admitted deadline after submission, and the
Windows operation-adapter job cap is 40 minutes.

Local Windows validation after the correction passed the complete sandbox-job
file (107 passed, 107 backend skips), the original-deadline proof selection
(8 passed, 8 backend skips), and three operation-workflow checks. Ruff and
format checks, Markdown links and diff validation passed. Hosted
requalification of the controlled clock and 40-minute lane remains pending;
S3-03 stays active, with no deferral. The implementation is committed at
`b701685`; at that exact source, audit/tracker validation passed (6 tests),
Markdown link validation passed, and Ruff, format and diff checks passed.

### A2A TLS credential-method matrix at `ec21cb2`

Expanded the actual-HTTPS official SDK credential tests from the prior
SendMessage-only examples to all seven advertised methods: SendMessage,
GetTask, CancelTask, ListTasks, GetExtendedAgentCard, SendStreamingMessage and
SubscribeToTask. The matrix exercises named bearer, API-key, OAuth client
credentials and mTLS. Bearer, API-key and OAuth cases fetch the public
AgentCard without business credentials, then send exactly one authenticated
business request to its selected endpoint. The mTLS cases require a trusted
client certificate; repeating each method without that credential fails
before another SDK dispatch.

At exact source `ec21cb2`, Windows validation passed the full SDK HTTP file
(121 passed); HTTPX emitted 63 existing CA-path deprecation warnings. The
implementation/tracker audit selection passed (6 tests), Markdown relative
links passed, and Ruff, formatting and diff checks passed. S3-02 remains
active: hosted requalification and the remaining peer-policy, recovery,
streaming and backend gates are still required.

### A2A card-policy rejection cross-products at `85aeb96`

Extended two negative card-authority witnesses across all seven advertised
methods. A configured protected-tool endpoint that differs from the endpoint
selected by the AgentCard, and an OAuth profile without an exact resource
endpoint grant, both reject before peer or token-issuer dispatch. This closes
those two method-level negative cross-products only; remaining policy and
backend combinations stay open.

At exact source `85aeb96`, Windows validation passed the full A2A SDK HTTP
selection (133 passed, with 63 HTTPX CA-path deprecation warnings), the
implementation/tracker audit selection (6 passed), Markdown relative-link
validation, Ruff, formatting and `git diff --check`. Hosted requalification
remains pending.

### A2A outbound stream retry policy at `4b29f81`

The A2A acceptance record now states the tested bounded policy explicitly:
outbound calls issue one request, lost or partial `SendStreamingMessage`
responses remain uncertain without replay, and resubscription is a separate
caller-requested `SubscribeToTask` operation for a known task ID. This does not
claim a durable resubscription lease; A2A08 expiry, malformed-event and
optional-method backend gates remain open. Evidence is retained in the
actual-TCP no-reconnect tests, the task-resubscription tests and the all-method
single-dispatch SDK test.

At exact source `4b29f81`, the implementation/tracker audit passed (6 tests),
Markdown relative-link validation passed, and `git diff --check` passed. The
associated SDK HTTP implementation selection passed 133 tests at parent source
`85aeb96`, whose implementation is unchanged here. Hosted requalification
remains pending.

### A2A stream expiry wording at `323696e`

The A2A08 evidence table now names the proven server-side stream timeout
separately from durable Task/receipt expiry, which remains unqualified. This
prevents bounded stream cleanup tests from implying a durable task-retention
policy. At exact source `323696e`, the implementation/tracker audit passed (6
tests), Markdown relative-link validation passed, and `git diff --check` passed.

### A2A initial-delivery phase mapping at `1f75bd1`

The A2A05 table now ties its three process-stop phases to the actual-SDK
producer/recovery fixture: post-ACK before native entry, after initial claim at
a partial native projection before the business action, and after the remote
receipt before native completion. Its positive matrix spans ADK/LangGraph and
SQLite/PostgreSQL and asserts preserved invocation identity/clock, generation
advance, one receiver commit and zero model calls; revoked skill, deleted
archive and damaged queue controls compare pre/post state. Competing dispatcher
and stale-owner races remain the explicit gap.

At exact source `1f75bd1`, the implementation/tracker audit passed (6 tests),
Markdown relative-link validation passed, and `git diff --check` passed. The
hosted batch at `f9cb76f` predates these A2A changes; this source needs its own
hosted run. This record does not mark A2A05 complete.

### Hosted PostgreSQL LangGraph recovery recurrence at `f9cb76f`

Hosted PostgreSQL CI run `38624744282`, job `116060928966`, failed the
`inbox-before-ack-postgresql-langgraph` case in
`tests/contract/test_selected_adapter_process_recovery.py` (220 passed, 158
skipped, 379 deselected, 1 failed). Its bounded output-shape diagnostics were
`[None]` at the native-execute, stored and result layers. An earlier PostgreSQL
attempt at run `38610573562` also failed this case; rerun job `116024883750`
passed it, so the recurrence is retained as an active qualification gap.

Review of the process-stop fixture found a race at the intended boundary: the
event was durably admitted before the ACK callback returned, but the callback's
wait yielded the event loop, allowing LangGraph's awakened listener to advance
before the parent stopped the producer. The fixture now freezes that loop at
the pre-ACK boundary. At source `0eee64b`, the focused local Windows
LangGraph/SQLite and ADK/SQLite selections each passed (one selected engine
case passed and the other engine was skipped). This evidence supports the
fixture correction but does not establish that it fully explains the
PostgreSQL mismatch. The hosted `f9cb76f` run predates the correction; hosted
requalification is required, and S3-03 remains open.

### Protected-approval recovery lease boundary at `f9cb76f`

CI run `38624744515`, Windows native timers/events/scheduling job
`116060931034`, failed one protected-approval process-recovery case after 717
passed and 769 skipped. The failing case was
`request-row-sqlite-langgraph`; the producer raised `InvocationStateConflict`
before it published the durable approval barrier. Its test-only start lease was
0.5 seconds, short enough to expire while the native producer was still being
scheduled. The 42-minute job otherwise reached completion within its 45-minute
limit.

At the same hosted checkpoint, both PostgreSQL operation-adapter jobs exercised
the protected-approval workflow and process-recovery selections. ADK's job
`116060928885` passed, and LangGraph's job `116060928966` passed those protected
approval cases but failed a separate S3-03 selected-adapter recovery case; see
the PostgreSQL recurrence above. The approval store's legacy PostgreSQL `REAL`
to `DOUBLE PRECISION` migration is present in the implementation.

The active correction increases the producer test lease to five seconds and
makes the recovery worker wait for natural expiry using the selected database's
clock before resuming. This keeps the producer renewable through its approval
barrier and preserves real post-stop lease expiry; it does not shorten the
workflow or approval deadline. The corrected LangGraph/SQLite protected
approval selection passed locally (73 passed, 94 skipped), and the formerly
failing `request-row` case passed in isolation. Exact-source hosted Windows
requalification remains pending; S3-05 remains open.

At exact source `62953c0`, the corrected Windows LangGraph/SQLite
`request-row` process-recovery case passed again (1 passed, 23 deselected in
21.86s). The full protected-approval selection on the same source passed 73
tests with 94 skips; the skipped cases require the other native SDK or a real
PostgreSQL datasource. Documentation links, the implementation/tracker audit
(6 tests), Ruff lint and formatting, and `git diff --check` passed. These local
results do not replace the hosted Windows requalification.

### Windows timing suite follow-up at `8183776`

Hosted CI run `38632792174`, Windows native timers/events/scheduling job
`116088355491`, completed with 715 passed, 769 skipped and 3 failed in
2,309.41 seconds. The protected-approval `request-row` case that failed at the
previous checkpoint passed on this source. The remaining failures were two
`test_retry_clock_and_rng_determine_actual_attempts[duration-sqlite-*]` cases
(ADK and LangGraph), which expected `model_error` but returned
`workflow_execution_error`, and
`test_killed_pattern_producer_retains_original_role_receipts_or_explicit_uncertainty[pipeline-known-sqlite-langgraph]`,
whose producer lost ownership before publishing its durable receipt barrier
with a five-second test lease. The retry mismatch also occurs in the completed
S2-04 selection and remains unexplained at this checkpoint; its new assertion
includes output, error, model-call count, sleeps, random draws and clock.

The agent-pattern producer test lease is now 15 seconds and the independent
recovery worker still waits for the recorded lease to expire naturally. At
source `8183776` plus this local correction, the focused pattern case passed,
and the full LangGraph/SQLite process matrix passed (10 passed, 30 skipped).
The complete retry-timing selections also passed locally on both engines (22
passed, 22 skipped each). These results do not establish a hosted fix; exact
source requalification is pending. S3-07 remains open, and the S2-04 retry
diagnostic remains under investigation.

All other hosted workflows at `8183776` passed: PostgreSQL, External Sandbox,
Agent Framework, Security and Docs. The remaining main-CI jobs, including root
quality and Windows operation-adapter recovery, passed; only this Windows timer
job failed.

At exact source `2143367`, the focused `pipeline-known` Windows
LangGraph/SQLite process-recovery case passed (1 passed, 39 deselected in
28.95s). The two hosted retry-duration cases passed locally on their respective
engines (2 passed, 42 deselected each). The full LangGraph/SQLite pattern
process matrix passed (10 passed, 30 skipped), and the complete retry-timing
selection passed on each engine (22 passed, 22 skipped). Markdown links,
implementation/tracker audit (6 tests), Ruff, formatting and `git diff
--check` passed. Hosted requalification remains required because the retry
duration output mismatch is not reproduced locally.

### Hosted PostgreSQL ADK operation-adapter timeout at `f235be4`

PostgreSQL CI run `38641211257`, job `116116878390`, terminated the ADK
operation-adapter selection with exit 124 at its fixed 20-minute command limit.
The progress log had reached 97%; the last visible case was
`test_real_stop_preserves_original_source_and_never_relaunches_uncertain_script[known-none-postgresql-adk-AdkWorkflowEngine]`.
No assertion failure or final pytest summary was emitted. The sibling
PostgreSQL LangGraph operation-adapter job `116116878511` passed. This is an
incomplete S3-03 validation result, not a code pass or a proven runtime defect.

The ADK operation job's command budget is raised to 25 minutes and its hosted
job limit to 30 minutes, retaining a finite bound and cleanup margin. The next
exact-source PostgreSQL run must confirm the remaining case and complete suite;
this was still pending at the `f235be4` checkpoint. The exact-source result
below records the successful `e45115c` rerun; S3-03 remains open for its other
acceptance gates.

### A2A acceptance follow-up at `dc45dc6`

The focused core A2A suite passed on Windows (176 passed, 64 deprecation
warnings in 123.68 seconds), including the card-selection, cross-binding
waiting-task resume, unknown/foreign task access, optional-method rejection and
partial TCP SubscribeToTask response-loss witnesses. The ADK/SQLite and
LangGraph/SQLite initial-delivery process-recovery selections passed 6 cases
each, with 6 cases skipped and 12 deselected per engine; those selections also
assert that the retained skill identity survives the process stop. The CI and
consolidation tracking selections passed 64 tests, including the new finite
PostgreSQL ADK operation timeout assertions. Markdown link validation,
implementation/tracker checks, Ruff, formatting and `git diff --check` passed.

No backlog checkbox advanced: A2A01-A2A08 remain open where their stated
cross-products and hosted evidence are incomplete, and S3-03 remains open until
the extended PostgreSQL ADK operation job is confirmed on a hosted rerun.

### A2A protected-resume and archive-rejection follow-up based on `e45115c`

The five focused core A2A files passed on Windows (217 passed, 64 warnings in
170.31 seconds). This includes a protected-approval resume-denial check on
JSON-RPC and HTTP+JSON, unknown/foreign task-access equality across both
bindings, and an actual timer wait whose later GetTask projection preserves the
committed artifact and remains immutable on repeated reads. The first timer
fixture attempt raced the initial execution lease; it now waits for that lease
to be released before dispatching the due timer.

The public resume archive/binding damage selection passed on ADK/SQLite
(12 passed, 36 skipped, 44 deselected in 81.71 seconds) and LangGraph/SQLite
(12 passed, 36 skipped, 44 deselected in 70.09 seconds). The matrix retains the
REST cases and now includes JSON-RPC and HTTP+JSON A2A resumes for missing and
digest-corrupt archive or invocation-binding evidence. Its first assertions
assumed an A2A local code in the JSON-RPC envelope and HTTP 500; the implementation
returns the generic JSON-RPC application error and HTTP 409 with the local code
in HTTP+JSON details, which the corrected assertions now verify.

The protected-resume test initially showed that a generic SendMessage could
enter and wait indefinitely inside a durable event listener. A2A target
resolution now rejects waiting invocations with a pending durable event listener
before native execution; the two protected-approval binding cases and the real
timer projection case pass individually, and the combined 217-test core suite
passes. This guard preserves timer/user-resume behavior and protects all durable
event waits from being resumed through an unrelated text message.

PostgreSQL CI run `38645586498` passed at exact source `e45115c`, including the
ADK operation-adapter job with a 25-minute test command and 30-minute job cap.
The remaining main CI jobs on that source are still running. No A2A or S3-03
backlog checkbox advanced.

### Integrated qualification batch at `bf7d507` — hosted gates pending

The full Windows root command collected the repository suite and completed in
7,343.91 seconds: 4,621 passed, 2,986 skipped and 13 failed; coverage was
90.49%. Four failures were CI-selector assertions that expected the same
PostgreSQL command budget for both engines. The workflow uses a 25-minute shell
and 30-minute job for ADK, and a 20-minute shell and 25-minute job for
LangGraph. The four selectors now assert those actual values; all four CI
selection files pass (20 tests).

The remaining nine failures were timeouts during this two-hour local run:
checkpoint wait-resume and completion recovery (ADK/SQLite), late invocation
ownership (ADK/SQLite), retry drain (LangGraph/SQLite), A2A timer projection,
and four bounded MCP stdio rejection cases. Replaying those affected test
functions together passed 95 cases with 88 environment skips in 355.98 seconds.
The suite therefore has no remaining reproducible failure from that local run;
the full root command should still be judged by the hosted CI run on the exact
source.

New malformed A2A Part/data resume rejection passed on both bindings, and the
in-process SQLite stale-generation fence passed. Owner-authority A2A resume
cases and the ADK expired-approval case require independent native SDK
environments and were skipped in the root environment; their hosted gates are
still required. The expired-approval fixture now leaves its normal 30-second
invocation lease independent from the approval's 0.5-second deadline, so slow
native setup cannot expire invocation ownership before the approval-expiry
observer is tested.

Ruff format/lint, strict mypy across 134 source files, Markdown link validation,
implementation-audit tests (4), root/core builds and `git diff --check` passed.
The feature branch and existing draft PR head both contain `bf7d507`; the CI,
PostgreSQL, External Sandbox, Security, Agent Framework and Docs workflows were
queued at the time this entry was written. No task or feature gate is advanced
by this local batch alone.

### A2A resume and route follow-up at `89d7836` — hosted qualification pending

This follow-up adds an HTTP+JSON rejection witness for all six optional A2A v1
operations, a real JSON-RPC nested-child timer/value Task projection, and
LangGraph native-checkpoint corruption coverage. The owner-authority resume
contract assertion now matches the existing fail-closed preflight response:
JSON-RPC `task_not_resumeable` and HTTP+JSON 409 with the same local code. The
no-mutation and secret-redaction assertions remain in place. A2A04 is still
partial: the HTTP+JSON child setup did not create its child-wait registration,
and child-fault projection is unverified.

The combined new A2A regressions passed 7 cases and skipped 12, with 92 unrelated
cases deselected. The skips are the owner-authority and native-checkpoint
cross-products that require isolated engine SDK environments and/or
`OWA_TEST_POSTGRES_URL`; they are not recorded as passes. The CI selection suite
first passed 69 cases while two PostgreSQL collection checks exceeded their
30-second subprocess limit during the long Windows run. Replaying those exact
parameter cases passed all four engine selections (4 passed, 45 deselected in
27.63 seconds), so no selection mismatch reproduced. Markdown relative links,
Ruff format/lint and `git diff --check` passed.

At the preceding source `bf7d507`, hosted CI reproduced only the stale
owner-authority response expectations in both engines and both database
backends; this source corrects that test to the established fail-closed
contract. The CI, PostgreSQL, External Sandbox, Security, Agent Framework and
Docs workflows must still qualify `89d7836` itself. S3-02 and all A2A gate
checkboxes remain open.

### A2A receipt, expiry and nested-child follow-up at `c5269bb`

The receiver-receipt witness now covers absent and malformed persisted effect
receipts across both A2A bindings, ADK/LangGraph and SQLite/PostgreSQL. It
collected 16 cases and skipped all 16 locally: SQLite selections need isolated
native SDK environments, and PostgreSQL selections need
`OWA_TEST_POSTGRES_URL`. Ruff format/lint, relative Markdown links and
`git diff --check` passed. The assertions therefore still require hosted
qualification.

A bounded HTTP+JSON diagnostic confirmed that `message:send` returns a working
Task and creates parent and child invocation/effect records, but after five
seconds both invocations remain running and the parent has no child-wait
registration. The temporary probe was removed because no safe narrow runtime
fix was established; the next step is to trace the HTTP+JSON dispatch/scheduler
path against the passing JSON-RPC nested-child value witness. HTTP+JSON child
projection and child-fault projection remain open.

The A2A08 expiry review found no durable Task, invocation, effect-receipt or
initial-delivery TTL or pruning API. EventStore's 24-hour retention is only for
eligible inbox arrivals and cannot be used as Task/receipt expiry because
recovery depends on retained evidence. No expiry behavior was invented or
tested. The acceptance record asks for an explicit profile exclusion or a
versioned retention contract before adding an expiry test. S3-02 remains active
and unchecked; these gaps do not block continuing its other ready evidence.

### Follow-up to the transient HTTP+JSON child-wait observation

The earlier HTTP+JSON diagnostic in the `c5269bb` entry stalled once with the
parent and child left `running`. A subsequent identical-payload comparison
exercised both transports with `returnImmediately: true`, the same catalog,
lifespan and engine, then checked the initial-delivery, timer, child-wait and
detached-child dispatcher error queues. Both bindings reached one registered
parent child wait with empty error queues (2 passed in 32.29 seconds). No
transport-specific runtime defect is established. The permanent child
projection test now covers JSON-RPC and HTTP+JSON with both terminal values and
faults; all four focused cases passed in 52.87 seconds. Repeated GetTask reads
preserve terminal payload and identity, and child fault details remain
sanitized. The separate-process A2A05 SQLite takeover witness also passed (1
case in 15.23 seconds); it settles one receiver commit under a higher owner
generation and rejects the paused old owner without changing its snapshot.

### Hosted CI findings at `8ff5f6e`

The exact-source CI run [38670669655](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/38670669655)
failed in the Windows native lifecycle job and the ADK/LangGraph contract jobs;
the Windows timer job was canceled after the aggregate failed. ADK failed four
receiver-receipt corruption cases because the fixture decoded a hashed identity
instead of its persisted JSON payload. LangGraph failed those same receipt
cases plus two native-checkpoint cases: HTTP+JSON requests omitted
`A2A-Version: 1.0`, and the JSON-RPC assertion expected an object while the
current error contract returns an ErrorInfo list. These were test-fixture
defects, not evidence of a production contract change.

The same Windows run exposed a LangGraph host-drain child-recovery fixture that
waited at an executor callback the native scope observer does not call. The
fixture now waits after the LangGraph child scope records its completed
receipt. The exact Windows test passed once on rerun after one transient
`InvocationStateError` about inconsistent recovery evidence; a direct worker
kill/recovery cycle also passed. The one transient failure retained only its
exception representation, so no more specific cause is established. All A2A
fixture corrections and the host-drain boundary correction are in the current
batch; this entry does not qualify them or mark S3-02 complete.

### Consolidated A2A batch at working tree based on `8ff5f6e` — 2026-10-17 (not green)

The locked root command
`uv run --locked pytest tests -vv --durations=20 --cov=core/src/open_workflow_agent --cov-report=term-missing --cov-fail-under=90 --cov-precision=2`
completed in 2h12m32s with 4,638 passed, 3,006 skipped, 15 failed and 90.47%
coverage. The failures were two ADK competitive-fork/scoped-flow ownership or
timeout cases; four nested-child projection polling timeouts (subsequently
addressed by widening fixture polling); eight internal sandbox/process cases
covering awaited-process cleanup, supervisor/job startup, deadline, resource,
output and mutation-fence timing; and one ADK CTK listener that remained
`running` instead of reaching `waiting`. This is not a
green root suite and does not qualify S3-02.

Focused follow-up passed all four nested-child terminal value/fault projections
for JSON-RPC and HTTP+JSON after fixture polling was widened to 30 seconds
(4 passed in 87.27s). The selected competitive-fork, scoped-flow and CTK cases
passed 12 (12 passed, 228 deselected in 30.99s). The selected Windows sandbox
rerun completed with 5 failed and 4 passed in 160.07s: both real-exit cases
hit the 15-second supervisor startup bound or returned `unknown`, both late
deadline/cancel cases observed `unknown`, and the workspace-quota case timed
out before its child marker. These remain actionable; no task is deferred.

The final static batch passed repository Ruff, format checking across 466
files, Markdown relative-link validation and `git diff --check`. Earlier in
this worktree, strict mypy, lock verification and root/core package builds also
passed; the recent fixture-only edits do not alter typed runtime source. Exact-
source hosted workflows completed for pushed commit
`aafe53cc3ae614ed561fb0cec2cb4dc9f639db06`: [CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39030788698),
[PostgreSQL CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39030788688),
[Agent Framework CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39030788689),
[External Sandbox CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39030788680),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39030788670),
and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39030788676).
PostgreSQL passed all ten jobs; Agent Framework, External Sandbox, Security and
Docs passed. CI failed four ADK and four LangGraph cases in
`test_a2a_resume_rejects_damaged_receiver_receipt_before_mutation`; both
bindings returned the sanitized `invalid_invocation_state` error after the
durable receiver-receipt proof was damaged, while the fixture expected a
non-resumable-task response. The workflow is waiting-input, so the generic
state-integrity error is the expected result. LangGraph also failed the
HTTP+JSON native-checkpoint corruption assertion because HTTP encodes the local
code as `metadata.owaCode`; JSON-RPC continues to use `metadata.code`. One
Windows LangGraph retry-timing case exceeded a 15-second outer test timeout;
the workflow's original 10-second deadline remains unchanged and the test's
outer harness ceiling is widened. The ADK Docker acceptance failure occurred
before build steps: Docker Hub returned HTTP 502 while resolving
`docker/dockerfile:1`.

The receiver-receipt and HTTP+JSON checkpoint assertions were corrected after
this run. A subsequent broad local run was stopped at 12% because its collected
test set predated those assertion and fixture changes; its early passing tests
are partial evidence only and it does not validate the current batch. The
supervisor integration-test deadline changes, outer retry harness timeout, and
A2A05 PostgreSQL process-race witness are also local follow-up changes. The
PostgreSQL workflow now selects the PostgreSQL process-race case, and a
workflow-selection test confirms that selection. Keep all A2A gate states
partial until a new exact-source batch and its hosted workflows pass.

### Latest working-tree root batch — 2026-10-17 (not green)

The latest consolidated command was
`uv run --locked pytest tests -vv --durations=20 --cov=core/src/open_workflow_agent --cov-report=term-missing --cov-fail-under=90 --cov-precision=2`.
It completed in 3h30m with 4,616 passed, 3,007 skipped, 28 failed, 13 errors
and 125 warnings. The configured 90% coverage gate passed. This run was on the
uncommitted worktree based on `aafe53c`, before the malformed-stream JSON
classification correction described below; it is not exact-source qualification
and does not close any task.

The 13 setup errors share one cause: the Windows `hot_rollback` fixture did not
observe SQLite's bounded real super-journal stop barrier after three 25-second
producer attempts. An isolated rerun reproduced the same fixture failure, so
this is not attributed solely to root-suite contention. The dependent SQLite
inspection tests could not start. The fixture mechanism remains actionable and
must be made reliable or the platform limitation precisely characterized before
claiming that acceptance gate.

The 28 failures included the new malformed-SSE-JSON assertion, two A2A
protected-approval polling cases that remained `WORKING` during the loaded run,
Windows sandbox/process supervision and stream timing cases, and several
workflow lifecycle cases. Focused follow-up passed both approval-wait cases
individually, all four nested-child projection variants, and the PostgreSQL CI
selection check. The malformed-JSON stream case initially returned
`invalid_response`; `_stream` now catches malformed UTF-8/JSON parse failures
and maps them to `invalid_stream`, matching the existing malformed-frame and
unsupported-event behavior. The corrected eight-case stream matrix passes, and
the canceled-Task repeated-GetTask projection passes for JSON-RPC and HTTP+JSON
(2 focused cases). The isolated super-journal fixture still fails as above.

The earlier 12%-stopped run remains a historical partial run and is superseded
as the latest full-suite validation; it does not qualify commit `36544b5`.
Exact-source hosted outcomes for `36544b5` are recorded below. The canceled-Task
projection follow-up is local and unpushed. S3-02 and A2A01-A2A08 remain open;
no task is deferred because there are actionable follow-ups.

## Exact-source qualification — `36544b5`

Commit `36544b56ff0d9421a9dca6c7dc5bc956cf3029ca` is pushed to
`codex/required-backlog`. Exact-source hosted workflow outcomes:

- [CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39051565929) failed. Root quality gates, deployment validation, mutation testing, Docker acceptance and Windows jobs passed. The ADK native/contract job had four failed cases (1,407 passed, 1,421 skipped): missing and malformed receiver receipts over JSON-RPC and HTTP+JSON.
- The same CI run's LangGraph native/contract job had six failed cases (1,461 passed, 1,392 skipped): two corrupt native-checkpoint cases and four missing/malformed receiver-receipt cases, all over SQLite and the two A2A bindings.
- [PostgreSQL CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39051565970) passed all ten jobs, including the initial-delivery PostgreSQL process-race witness.
- [Agent Framework CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39051565945), [External Sandbox CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39051565907), [Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39051565978) and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39051565906) passed.

The corruption-resume cases return the expected sanitized `invalid_invocation_state`,
but their before/after snapshots show common invocation and ownership revisions
changed before rejection. This is an actionable shared resume-ordering defect;
the cases do not qualify. The 3h30m local root result above ran before this
commit and also remains non-green. No S3-02 task or A2A gate is complete.

## Local follow-up batch — 2026-10-17 (not qualified)

The common resume path now verifies retained receiver/native state before
acquiring durable execution ownership, then repeats the verification under the
lease to guard against concurrent changes. The targeted LangGraph/SQLite
corruption selection produced one failure followed by a Windows pytest
reporting-thread access violation; it exited without a usable test summary.
No native-profile case is counted as passing. The exact source and failure are
in the preceding hosted qualification record.

The A2A04 tests add stable, read-only repeated Task projections for cancellation
(JSON-RPC and HTTP+JSON: 2 focused cases) and completed END/persisted deadline
fault (same bindings: 4 focused cases). The deadline case seeds the terminal
fault and does not exercise the clock transition. Together, the focused route
and common version-recovery selection passed 15 cases and skipped 100 because
optional backend environments were absent. Ruff, format, documentation-link
validation and mypy passed after formatting the added tests. The wider root
suite and exact-source workflows have not run on this batch.

## Exact-source qualification — `d7a3acf`

Commit `d7a3acf99d6a62f17fac23de7bbece48e854558b` is pushed to
`codex/required-backlog`. [PostgreSQL CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39059740959),
[External Sandbox CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39059740828),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39059740908)
and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39059740818)
passed. Main [CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39059740893)
and [Agent Framework CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39059740920)
failed on the same recovery-admission shutdown assertion: read-only resume
preflight now runs before lease acquisition, while the test expected only
lease-owned tasks to be tracked. The failures were two each in root/ADK/LangGraph,
two in Windows ownership, and three in Agent Framework. The current working-tree
change tracks and cancels preflight admission tasks separately from leased
ownership tasks; the focused ADK/LangGraph contract selection passes locally.

The same CI run exposed two independent Windows fixture issues: the
authenticated autonomous-deadline test received a `PermissionError` while
reading `heartbeat.json` (Windows operation-adapter job: 961 passed, 454
skipped, one failed), and the native SQLite inspection fixture did not expose
its real hot-super-journal barrier, producing 13 setup errors in the
jq/admission selection. The jq/admission assertions showed no independent
semantic failure. Both fixture issues remain under investigation and are not
qualified or deferred. The current exact-source result does not close S3-02.

The SQLite fixture investigation rejected several bounded strategies with
real-producer evidence: Windows byte-range locking caused SQLite `disk I/O
error`; 8/32 MiB timestamp polling and 64 MiB direct-byte sampling did not
observe a changed database page while the master journal was valid; a 127 MiB
native database plus journal approached the 256 MiB inspection snapshot cap
and did not produce a sample; small-cache spill wrote marker bytes before the
master existed; and a directory-notification probe did not terminate cleanly.
The fixture is restored unchanged. The next avenue is a proven SQLite VFS-level
observation barrier; no recovery assertion is weakened and S3-02 remains active
for its other actionable gates.

## Exact-source qualification — `5f11067`

Commit `5f110670f19c001ae812dc5228c17aeb0c1958e7` is pushed to
`codex/required-backlog` and PR #64. Its serialized focused batch passed seven
cases across resume shutdown, repeated host-drain Task projection, real
workflow-deadline transition and IPC PermissionError retry. The host-drain
event wait was raised from five to thirty seconds after an initial run competing
with static checks timed out both bindings; the isolated rerun passed, then the
serialized batch passed. The deadline witness advances the engine's absolute
clock past the configured workflow timeout, observes the persisted fault, then
verifies stable/read-only repeated GetTask results and zero model calls on both
bindings. The IPC selection passed three cases with one skip. Ruff, formatting,
mypy, documentation links and diff checks passed locally before publication.
Unary gRPC client tests passed 77 cases locally; its cross-engine/backend and
hosted gates remain open. No broad local root suite has been rerun, and no Stage
3 task is complete from this evidence.

The exact-source run set completed with these outcomes:

- [Main CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39068459469)
  failed only the Windows operation-adapter job (960 passed, 454 skipped,
  three failed). The failed `test_observation_denied_open_rechecks_proofs_with_bounded_wait`
  variants showed that applying the new read-open retry to every IPC frame
  multiplied deliberate observation-proof rechecks.
- [PostgreSQL CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39068459458)
  failed one ADK recovery case (357 passed, 1,118 deselected): when timer delivery
  won the manual-resume race, the due-timer path left `$resume_input` unbound and
  the downstream expression faulted.
- [Agent Framework CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39068459460),
  [External Sandbox CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39068459488),
  [Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39068459451)
  and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39068459463)
  passed. Main CI's root quality gates, native ADK/LangGraph suites, Windows
  timer/checkpoint/lifecycle/ownership lanes and Docker acceptance also passed.

The current working batch narrows read-open retries to explicit heartbeat
reads, binds or clears `$resume_input` for every inline and durable wait, adds a
late-result host-drain guard and extends the A2A host-drain/socket-result
witnesses. The grouped local regression run passed 28 cases and skipped two
backend-inapplicable cases; the selected checks included the existing timer
race, admission shutdown, both IPC permission behaviors, all A2A host-drain
variants, the actual deadline transition and seven SDK methods over ASGI/TCP.
Ruff, formatting, strict mypy, Markdown links and diff checks also passed
before the last documentation edits; the documentation validator will be rerun
with the final batch. Hosted requalification is pending. The original Windows
autonomous-deadline scenario awaits confirmation. The SQLite hot-super-journal
fixture remains unresolved as described above, and no recovery assertion was
relaxed.

## Exact-source qualification — `a77678a`

Commit `a77678a12375215f1964b86dd922138b3dcee06e` is pushed to
`codex/required-backlog` and PR #64. The grouped local regressions passed 28
cases with two backend-inapplicable skips. Ruff, repository formatting, strict
mypy over 132 source files, Markdown relative-link validation and `git diff
--check` passed. This includes the two fixes exposed by the prior hosted run:
heartbeat retries are opt-in for heartbeat reads so observation-denial proof
rechecks remain unchanged, and inline/durable waits bind `$resume_input` to the
resume value or `None` for every completed wait. A2A host drain now checks for
late returns after suppressed cancellation; the A2A SDK method witness asserts
typed result fields across all seven advertised methods over ASGI and loopback
TCP.

Exact-source hosted runs: [PostgreSQL CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39072219292),
[Agent Framework CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39072219309),
[External Sandbox CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39072219313),
[Security](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39072219288)
and [Docs](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39072219289)
passed. Main [CI](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39072219326)
completed with one failure in Windows operation-adapter recovery: 962 passed,
454 skipped, and the LangGraph host-drain recovery witness failed during resume
with `InvocationStateConflict`. All other Main CI jobs passed. The exact failure
is under focused correction; `a77678a` is not yet hosted-qualified and S3-02
remains open.

### Local fixture and A2A follow-up based on `a77678a`

The follow-up adds a pinned APSW test dependency and pauses the producer inside
the SQLite VFS `xDelete` callback immediately before deleting the hot
super-journal. The parent kills the producer at that commit boundary and keeps
the original rollback and immutability assertions. The focused batch covering
`tests/core/test_native_sqlite_inspection.py`, the new host-drain stream cases,
and the initial-delivery process-race file passed **18 tests with two
backend-inapplicable skips** on Windows. Ruff checks for the edited tests,
repository format validation, Markdown link validation, lock consistency and
`git diff --check` passed. This is local evidence only; hosted Windows and
PostgreSQL requalification remain pending.

The same batch plus the exact hosted LangGraph drained-background-job case
passed 18 tests with three skips in the root environment; that case skipped
because the independent LangGraph SDK is not installed there. In the isolated
LangGraph environment, two attempts failed before the recovery boundary: the
producer timed out while starting the child job (once waiting for the child
ready marker and once before `_execute_background` returned), leaving no
`sandbox_jobs` row. These local setup timeouts do not verify the recovery-lease
adjustment. The hosted case failed later, during recovery lease verification,
and requires the post-fix hosted rerun for qualification.

## Exact-source follow-up — `bc5f551`

Commit `bc5f551bb8c2157c2293efe3ab5fd08da142349e` is pushed to
`codex/required-backlog` and PR #64. The batch adds the pinned APSW test
dependency to the Linux engine lifecycle matrix and Windows LangGraph lifecycle
command. Those commands select isolated engine environments, so the root
development group does not provide APSW to the child process that constructs
the real SQLite super-journal fixture. A CI workflow assertion covers both
install commands. The batch also extends the A2A host-drain projection witness
to a late provider fault, preserving its persisted-running, stable WORKING
projection, payload and model-call assertions.

The grouped local batch passed **24 tests with one backend-inapplicable skip**.
It covered the actual APSW VFS super-journal barrier, both bindings for late
host-drain values/faults and stream disconnect, the SQLite initial-delivery
process race, and the lifecycle workflow guard. Ruff, formatting, and
`git diff --check` passed. A separate locked LangGraph environment check
confirmed an APSW import from a child process. No broad root suite was rerun.

The preceding exact-source run at `18123a6` passed PostgreSQL CI, Agent
Framework CI, External Sandbox CI, Security and Docs, but Main CI failed in
three native lifecycle jobs because those isolated child processes could not
import APSW. The failure was a test-environment dependency omission, not a
runtime assertion failure. On `bc5f551`, the isolated Windows, ADK and
LangGraph lifecycle jobs passed with APSW installed; PostgreSQL CI, Agent
Framework CI, External Sandbox CI, Security and Docs also passed. Main CI
failed its root suite only because a selector guard still expected one
PostgreSQL process-race test after the second witness was added. Its Windows
timer lane was cancelled at 45 minutes after reaching 95% progress. The
follow-up batch, later pushed as `8f70acd`, updates the selector guard, extends
that job bound to 60 minutes, and adds A2A06 corruption/terminal-projection
selections with explicit workflow guards. At the time this record was prepared, Docs
([run](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39080070098))
and Security
([run](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39080070121))
passed, along with PostgreSQL
([run](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39080070133)),
Agent Framework
([run](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39080070059))
and External Sandbox
([run](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39080070212)).
CI [run](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39080070127)
did not pass for the selector-guard and timer-limit reasons above. These checks
do not close S3-02. The earlier `a77678a` Windows operation-adapter host-drain
recovery failure remains subject to hosted confirmation. On 2026-10-20 the
finite S3-02 scope decision explicitly excluded durable Task/receipt expiry;
no expiry capability is advertised.

## Initial grouped S3-02 acceptance batch — pushed as `8f70acd` on 2026-10-20

The `8f70acd` batch added a seven-method correlated JSON-RPC error
matrix and a seven-method legacy-version/unsupported-card-binding rejection
matrix; strengthens `returnImmediately` identity, structured-output and
single-dispatch assertions on both inbound bindings; adds protected-approval
decision-by-another-worker and expiry-dispatcher-restart recovery witnesses;
and rejects malformed streaming messages before invocation or subscription on
both bindings. It also corrects the stale PostgreSQL selector guard, extends
the Windows timer workflow bound from 45 to 60 minutes, and schedules A2A06
corruption/terminal-projection witnesses in Windows and PostgreSQL lifecycle
CI. The A2A acceptance record now states that durable Task/receipt expiry is
outside this finite profile and is not advertised.

The grouped local root selection passed **344 tests with 2 backend-inapplicable
skips** in 260.24 seconds. It included the updated A2A async, SDK HTTP, inbound
v1, route, child projection, optional-method, process-race and CI guard suites.
The isolated ADK receipt-corruption selection passed **4 with 12 matrix skips**;
the isolated LangGraph native/receipt corruption selections passed **6 with 14
matrix skips**. The additional isolated-engine skips reflect backend/transport
combinations not available to those local profiles and are not promoted to
acceptance; PostgreSQL and complete engine matrices remain in hosted CI.

Markdown relative links passed. Ruff lint passed and all 549 Python files passed
format checking after formatting the two touched A2A test files. `git diff
--check` passed. These local results did not qualify the then-current source;
exact-source hosted checks and review of their full engine/backend matrices are
pending. S3-02 remains open. No task is deferred.

After that grouped batch was pushed, the post-push selector audit found that
the six JSON-RPC and six HTTP+JSON optional-operation rejection cases were in
the full Linux root suite but not in the dedicated Windows common A2A suite.
Commit `ef43ab7` adds both test files to `windows-mcp` and its selector
guard. The guard passed locally; exact-source hosted results are recorded below.

## Exact-source security follow-up — `ef43ab7`

Security workflow run [39349622796](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39349622796)
found three fixable advisories in `aiohttp 3.14.3` in each of the root,
standalone-core, ADK, LangGraph and Agent Framework resolved environments. The
reported fixed release was 3.14.4. Commit `a7176a0` updates every
active lock to `aiohttp 3.14.5` and its compatible `yarl 1.25.1`; frozen
migration reference lock sources remain unchanged.

After the lock update, the grouped A2A/root selection again passed **344 tests
with 2 backend-inapplicable skips**. Isolated ADK receipt corruption passed 4
with 12 matrix skips; isolated LangGraph native/receipt corruption passed 6
with 14 matrix skips. `uv lock --check` passed for all five active locks, as
did Markdown link validation, Ruff lint/format, and `git diff --check`. The
exact-source security audit passed for all five active runtime environments
and both controllers in run
[39350449842](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39350449842).
The exact-source full CI requalification is tracked in the `bd5cf79` record
below.


## Exact-source A2A expansion — `bd5cf79`

The batch extends the A2A01 card/version matrix with a legacy-first positive
fallback case; covers A2A03 deployed-skill routing and fail-closed unknown or
missing skills on JSON-RPC and HTTP+JSON; drives the A2A04/A2A08 cross-worker
terminal stream through both public SubscribeToTask routes; and proves A2A07
rejects an unapproved card-selected endpoint before authentication preparation
or business send. HTTP+JSON skill errors now map `skill_not_found` to
`INVALID_PARAMS` (code 3) instead of the generic internal code. A2A05's live
owner process-race test is selected in Windows native lifecycle CI, while its
PostgreSQL helper is included in the PostgreSQL workflow path filter. Durable
Task/receipt expiry remains outside the finite S3-02 profile by the user's
2026-10-20 scope decision.

The combined local regression batch passed **265 tests with 25
backend-inapplicable skips** in 104.83 seconds. It included the A2A SDK HTTP,
inbound v1, skill, bounded-memory and process-race tests plus CI selection
checks. Ruff lint passed; six touched Python files passed format checks, and
`git diff --check` passed. The A2A SDK suite reported 63 existing httpx TLS
`verify=<str>` deprecation warnings. PostgreSQL was unavailable locally, so its
process-race parameter skipped.

Exact-source hosted runs were started for this commit: CI
[39353140943](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39353140943),
PostgreSQL CI
[39353140881](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39353140881),
Agent Framework CI
[39353141210](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39353141210),
External Sandbox CI
[39353140879](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39353140879),
Security
[39353141134](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39353141134),
and Docs
[39353140886](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39353140886).
At record creation, Docs passed and the remaining hosted workflows were queued
or running. These results do not close S3-02; record final outcomes here after
the exact-source batch finishes.

## Grouped A2A gate coverage follow-up — 2026-10-20

The `bd5cf79` exact-source main CI run completed its Windows common MCP/A2A
selection with 537 passed and 43 skipped, but one TCP streaming loss case failed
because the three-second outer test wait expired at the same boundary as the
client's three-second network timeout while Windows TLS cancellation was still
finishing. The client timeout remains three seconds; the test now gives the
result assertion a ten-second observation window. The same focused test passed
on the local Windows host.

The grouped follow-up adds a second-process public `GetTask` check for completed
and faulted projections, bounded cross-worker subscriptions, exact native A2A
tool request/receipt distinctions, official SDK checks for `GetExtendedAgentCard`
and the profile's pre-dispatch `ListTasks` rejection, a simultaneous
initial-delivery process claim race, and CI selector guards. Durable Task/receipt
expiry remains excluded from the finite S3-02 profile by the user's 2026-10-20
scope decision. No acceptance gate or backlog item is marked complete.

The common local batch passed **263 tests** with 69 expected httpx TLS
`verify=<str>` deprecation warnings. The ADK and LangGraph contract batches each
passed **25 tests** with 69 matrix skips; PostgreSQL was not configured locally,
so the hosted PostgreSQL lane remains required. Ruff lint passed, all 467 Python
files passed formatting, Markdown link validation passed, and `git diff --check`
passed. The focused Windows timeout-boundary regression also passed.

The timeout correction and grouped coverage follow-up are awaiting exact-source
hosted CI, PostgreSQL, security, documentation, Agent Framework and external
sandbox results. The prior `bd5cf79` Security, Docs, Agent Framework, External
Sandbox and PostgreSQL workflows passed; those results do not substitute for
requalification of the follow-up source.

The same root run also exposed two stale CI guards: the implementation audit
still named the previous public-subscription test symbol, and the authority and
protected-approval selectors expected a 45-minute Windows timing job after its
budget had been raised to 60 minutes. Commit `9202774` updates the audit anchor
and both selector expectations; no behavior or acceptance status is inferred
from these selector corrections.

## Exact-source CI guard refresh — `9202774`

This source corrects the two stale root CI selectors and includes the Windows
TCP timeout-boundary fix from `fa778a1`. The focused Windows regression passed
locally, and the targeted CI guard/audit selection passed 18 tests. The local
Ruff, format, documentation-link and diff checks passed on the preceding
grouped coverage batch; these do not qualify the later uncommitted SDK tests or
context-rejection matrix.

For exact source `920277422db473a7e637a05286680c72097326c3`, Docs run
[39356197281](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39356197281)
and Security run
[39356197293](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39356197293)
passed. At the latest check, CI run
[39356197225](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39356197225)
had passed mutation testing, both Docker acceptance jobs, deployment manifest
validation, Windows jq/admission and transport/authentication, both native
lifecycle/version suites, native task/checkpoint/routing, and native
ownership/effect recovery. Root quality, Windows timers, Windows operation
adapters, ADK native/contract and LangGraph native/contract jobs were still
running. Agent Framework run
[39356197239](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39356197239)
passed. PostgreSQL run
[39356197267](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39356197267)
passed. External Sandbox run
[39356197258](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39356197258)
passed. No failed job was observed at that check. The main CI was later
cancelled after commit `447432b` superseded it. These partial outcomes do not
qualify S3-02; record exact-source outcomes from the superseding batch.

## Grouped A2A SDK and resume matrix — 2026-10-20

The local batch adds an official SDK `ListTasks` rejection to the inbound
matrix, an HTTP+JSON SDK exact-error/zero-dispatch witness, six actual-TCP
malformed-reply and AgentCard-order cases, and a context-mismatch rejection
matrix that verifies native checkpoint and delivery state remain unchanged.
The user's scope decision explicitly excludes durable Task/receipt expiry from
the finite S3-02 profile; no expiry capability is advertised.

The grouped root selection passed **329 tests with 36 expected environment
skips**. Isolated ADK and LangGraph selections each passed **9 applicable
SQLite cases with 9 engine/backend skips**; PostgreSQL was unavailable locally.
Ruff, formatting, Markdown-link validation and `git diff --check` passed. These
are local results for the current worktree, not hosted qualification. The
PostgreSQL variants are selected in the hosted lifecycle lanes. No A2A gate or
backlog task is marked complete.

The batch was pushed as `447432b`. Its Docs, Security, PostgreSQL, Agent
Framework and External Sandbox workflows were queued or running at the latest
check. Main CI run
[39358262414](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39358262414)
is in progress and exposed a pytest import-name collision between the core and
contract `test_a2a_http_json_optional_methods.py` modules in root and Windows
common collection. The contract module has been renamed to a unique SDK-specific
filename, and the HTTP+JSON SDK witness is now selected by both PostgreSQL
lifecycle jobs. Requalification is pending.

## A2A response, terminal-projection and corruption follow-up — 2026-10-20

The current worktree extends actual-TCP AgentCard precedence to all seven
advertised methods against legacy-version-first and unsupported-binding-first
cards (14 cases). It adds a cross-worker terminal Task read in both bindings
while an outbound `call:a2a` effect remains pending, asserting repeated reads
preserve the terminal payload and uncertain receipt without invocation/model
replay. The native-checkpoint corruption witness now also checks that rejection
starts no active LangGraph run, execution lease or ownership task. A new
official-SDK malformed streaming admission matrix covers ADK/LangGraph,
SQLite/PostgreSQL and both inbound bindings, checking invocation, effects,
delivery, subscription and model state remain unchanged.

These are implementation additions only; A2A01, A2A04, A2A06 and A2A08 remain
partial. Their consolidated local results and exact-source hosted qualification
state are recorded below.

The first consolidated worktree run then completed with **17 failed, 349 passed,
and 49 skipped**. Fourteen actual-TCP card cases bypassed validated-card
admission instead of fetching and selecting the fixture AgentCard; the case now
uses the production card-fetch/selection path. Two PostgreSQL selector guards
filtered the generic HTTP+JSON case by a non-applicable engine id, and the
native lifecycle guard omitted that fixture; the selector assertions and
fixture list now match the hosted commands. These failures were actionable and
have corrections in the current worktree. Consolidated requalification is
pending.

After those corrections, the grouped local batch passed **365 tests with 49
environment skips**. The only remaining failure was the workflow guard for the
Windows/ADK/LangGraph native lifecycle selector set; the two new SDK contracts
are now selected in each native lane and excluded from the generic engine
matrix. The focused guard passed. A separate isolated-engine run found that
the SDK raises `InvalidParamsError` for malformed streamed input rather than
the broader client error the first assertion expected; after correcting the
assertion, the malformed-streaming ADK/SQLite and LangGraph/SQLite cases passed
(2 each), as did the LangGraph native-checkpoint corruption SQLite cases (2).
Ruff, format, documentation-link and diff checks passed. PostgreSQL execution
still requires hosted lanes; no S3-02 gate is closed.

## A2A authority, uncertain-effect and stream follow-up — 2026-10-20

Commit `58c102c` on top of `f3d399b` adds actual-socket typed errors and
tenant-first card selection, authenticated-owner cancel/subscribe/resume,
archived-v1 skill recovery after reopen, malformed public stream-event
handling, cross-origin bearer/OAuth rejection, and native-engine cross-worker
host-drain coverage. Its Docs and Security workflows passed; CI, PostgreSQL,
Agent Framework and External Sandbox were still active at the initial check.
All six workflows have since completed: PostgreSQL, Agent Framework, Docs,
Security and External Sandbox passed; main CI failed three Windows sandbox
stdio cases where a 0.5-second test deadline expired during process startup or
stream validation. The current uncommitted batch adds a seven-method actual-TLS
foreign-origin rejection witness, a PostgreSQL selector guard for A2A06
recovery witnesses, terminal Task resubscription projection through the
official SDK, and a wider five-second fixture deadline for malformed/output
cases (stall/partial-stream cases retain 0.5 seconds). Durable Task/receipt
expiry remains excluded from the finite S3-02 profile by the user's decision;
no expiry capability is claimed.

The first native host-drain regression run found that a cancellation-resistant
provider could return after host drain and have its effect receipt marked
completed while the common Task stayed working. Core now checks lifecycle
authority before storing successful or failed effect outcomes, leaving a late
result unresolved for recovery. Isolated native ADK and LangGraph tests now
pass this cross-worker late-result case for both A2A bindings.

Final local grouped validation on this working tree:

- A2A, recovery, public-route and CI-selector root batch: **378 passed, 149 skipped**.
- Isolated native host-drain checks: **ADK 2 passed/2 engine-specific skips**;
  **LangGraph 2 passed/2 engine-specific skips**.
- Ruff lint and formatting, mypy (130 source files), documentation links and
  `git diff --check` passed.

The expanded local qualification batch on this worktree completed the full
Windows root suite with **4,821 passed, 3,074 skipped, and 90.53% coverage**;
this run began before the sandbox fixture deadline adjustment. After that
adjustment, all six bounded stdio cases passed. The A2A SDK HTTP and PostgreSQL
selector root selection passed **330 tests with 2 skipped**; the isolated ADK
and LangGraph A2A inbound/archive-resume selections each passed **36 tests**.
Ruff, formatting, mypy (133 source files), documentation links and diff checks
passed on the final worktree. The new exact-source hosted run remains pending.

The pushed source `f3d399b` still has a failed PostgreSQL workflow. Its three
failures are the `ListTasks` rejection assertions in two SDK-binding cases and
the HTTP+JSON case; each expected a globally empty shared invocation table but
found 164 unrelated rows from earlier tests. The local correction compares the
table count before and after each rejected request. Docs, Security, External
Sandbox and Agent Framework passed at `f3d399b`; the aggregate CI workflow was
still running at the latest check. The corrected working tree has not yet been
hosted-qualified. No S3-02 task or A2A gate is complete or deferred.

## Exact-source follow-up at `cf59700` and current operation-adapter batch — 2026-10-20

The `cf59700` hosted PostgreSQL and Agent Framework workflows both failed the
observed-effect contract after an authorized business cancellation: the
provider had returned a known result, but the durable effect receipt remained
pending. The worktree correction allows the known external result to be
checkpointed after business cancellation, while keeping host-drain and
ownership-loss fences. Later workflow checkpoints still honor cancellation.
This correction is locally exercised by the existing cross-worker contract
cases and has not yet been hosted-qualified.

The `cf59700` main CI run was superseded and cancelled after commit `bd934ee`
started newer workflows. Its ADK and LangGraph native lifecycle jobs failed
only the same observed-result cancellation case. Windows task-checkpoint CI
failed one LangGraph SQLite `root` scope: it did not observe `TaskWaiting`
within the test's 10-second deadline, while the other five SQLite scopes
passed (281 passed, 55 skipped, 1 failed). This was an independent timing
failure, not an effect-receipt failure. The current follow-up raises that
test's bounded wait and resume deadlines to 30 seconds; its hosted and local
requalification are pending. The other in-progress native and Windows jobs
were cancelled with the superseded run.

The `cf59700` main CI run
[39364230331](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/39364230331)
was cancelled after exposing these failures. In the `bd934ee` workflows, Docs,
Security and External Sandbox passed. Both required Windows gRPC matrices and
both Windows background matrices passed. Both principal native lifecycle jobs,
the Windows native task-checkpoint and receiver-recovery jobs, PostgreSQL
LangGraph lifecycle/native/operation-adapter jobs, Agent Framework CI, and all
PostgreSQL CI jobs also passed. At the previous check, the Windows operation-
adapter lane, remaining principal contract/timer lanes and root quality were
still running. The Windows operation-adapter suite later passed. The remaining
lanes then completed with three failures detailed below. These partial results
do not qualify the full new source or close S3-02/S3-03.

Commit `bd934ee` adds required-engine selectors and Windows
SQLite matrices for gRPC and background recovery, a capability-positive gRPC
test, and an internal default-false background qualification gate. It also
adds recovery coverage for a producer stopped after the supervisor records a
terminal observation but before the durable terminal receipt is written. The
gate prevents background execution from being admitted or advertised before
the finite profile is accepted.

Local validation on this batch passed: the scoped root selection **140 passed,
12 skipped**; gRPC SQLite contract/process recovery **22 passed on ADK and 22
on LangGraph**; background process recovery **13 passed on ADK and 13 on
LangGraph**; optional Agent Framework host-drain selection **20 passed, 36
skipped**; pure AsyncAPI descriptor selection **134 passed**. Ruff lint/format,
mypy (131 source files), documentation validation and `git diff --check`
passed. AsyncAPI remains transport-inactive; no broker behavior is qualified.
The new hosted Windows matrices and exact-source requalification remain
pending. Durable A2A Task/receipt expiry remains
excluded from S3-02 by the user's scope decision; no expiry capability is
advertised. S3-02 and S3-03 remain unchecked, and no task is deferred.

The completed hosted run `39366187632` for `bd934ee` ended with three failures:
the Windows LangGraph event/child recovery case exceeded its 15-second wait
for a slow child subworkflow; the Windows LangGraph deadline recovery case
exceeded its 10-second native startup wait; and the root A2A timer test saw a
working Task after its dispatch worker exited. The first two failures expired
their test synchronization bounds, with neighboring cases passing. The A2A
dispatcher records caught errors in a private queue, which the old test did not
inspect. Correction commit `923302f` raises both native startup bounds to 30
seconds, adds assertions that surface recorded dispatcher errors and persisted
nonterminal state, and widens the earlier LangGraph native task recovery waits
to 30 seconds. The corrected timer, task-checkpoint, ownership/effect, and
operation-adapter Windows lanes passed. The full root and ADK/LangGraph suites
passed, as did PostgreSQL, Agent Framework, Docs, Security and External
Sandbox. External Sandbox initially failed its LangGraph image build because
the Alpine package index returned a TLS error; that failed image job passed on
rerun. All six exact-source workflows are green at `923302f`. No S3-02 or
S3-03 task is complete from this batch alone; A2A05's finite process-recovery
gate is verified, while the other A2A gates and broader adapter acceptance
remain open.

## A2A native owner-read, PostgreSQL host-drain, and receipt-read follow-up — 2026-10-20

This local candidate is based on pushed source `ab83bbd`. It adds native
foreign-principal `GetTask`/`SubscribeToTask` checks on ADK and LangGraph, extends
late-provider host-drain coverage to actual PostgreSQL in both engine lanes,
and repeats public `GetTask` after known-receipt recovery through JSON-RPC and
HTTP+JSON. The PostgreSQL workflow selectors include the new host-drain cases;
the existing selectors already collect the new owner-read and receipt-read
cases.

The focused SQLite selections passed on each principal engine: **6 passed,
18 skipped** for ADK and **6 passed, 18 skipped** for LangGraph. The skips are
the PostgreSQL variants, which require hosted database services. Ruff lint,
format, documentation links and `git diff --check` passed. An initial ADK
invocation omitted its required native extra and collected 24 skips; rerunning
with the declared engine extra produced the passing result above. No task or
gate is complete from these tests alone. Fresh exact-source hosted CI and the
PostgreSQL selections remain pending; durable Task/receipt expiry stays
excluded from the finite profile.
