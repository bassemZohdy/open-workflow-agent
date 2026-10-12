# S2-06 timer and schedule acceptance

Status: implementation and finite acceptance are in progress. This record does
not qualify a capability or mark a backlog task complete. `Project Definition.md`
and TODO S2-06 own the scope: durable timers, missing schedule and event-trigger
semantics, and releasing sleeping workflow executions.

The public DSL remains the unchanged Open Workflow 1.0.3 schema at upstream
`2dd2c84170d5f3e05d58e913e9ca298dcf8d543a`, SHA256
`704ef5e91c5d823167dd8751794edb1dd1a6f9a3bdf9bfd389bf9c6b23ae3816`.
The reviewed release semantic reference is immutable tag revision
`9b5b1da29e9d4fff2358580241e11aab22704a16`. Its Schedule and Wait sections define
the semantic oracle; the schema snapshot and release reference have distinct
provenance. In particular, `every` admits interval-based runs while previous
runs remain active, `after` repeats following completion plus its delay, and
`schedule.on` starts a new invocation from triggering events.

## Finite requirements

The eight requirements below cover the existing preparation cases C01-C16 and
mapped inventory A087/A088/T19, A101/A102/AS007/AS025/T25, P-AS007/P-AS025,
T59 and T60. Tests must use real common invocation handles; a fabricated ID or
an in-memory mutex is not evidence of atomic creation or a database claim.

| Requirement | Required behavior | Concrete acceptance | Preparation cases |
| --- | --- | --- | --- |
| TMR01 | Persist the original evaluated due time; suspend native execution without a resident per-wait sleeper; use remaining time after reopen. | Shared 60/20/40 clock scenario and a duration above 300 seconds; original input and preceding action preserved; actual ADK/LangGraph process stop and native reentry; distinct loop occurrence. | C05, C09 |
| TMR02 | Registration, native acknowledgement, claim and wake decisions reconcile without duplicate continuation. | Failures before/after arming and native waiting acknowledgement; two claimants, stale generation, firing receipt before continuation, duplicate wake and early manual/due race. SQLite and actual PostgreSQL. | C01, C02, C05, C06, C07 |
| TMR03 | Cancellation and original deadline/retry budgets remain authoritative while suspended. | Cancel followed by late due delivery; terminal invocations do not reopen; task/workflow deadline expiry, original retry delay/jitter/count evidence, and transient OS stop remain distinct. Parallel waits retain each original due time; competitive waits publish no winner while all branches remain suspended. Existing recovery and ownership guards stay enabled. | C07, C08, C15, C16 |
| SCH01 | `every` overlaps without shifting cadence; `after` repeats relative to its predecessor's committed completion. | A blocked first run and controlled subsequent slots; no early `after` successor; reopen around completion/successor creation; explicit fault/cancel policy. | C10, C11 |
| SCH02 | Supported cron grammar, timezone, DST and missed-slot policy are explicit and durable. | Valid/invalid admission; UTC and Asia/Dubai boundaries; DST transition fixtures; persisted nominal slots and declared bounded misfire behavior after reopen. Timezone is deployment policy, never an added DSL property. | C12, C16 |
| SCH03 | `schedule.on` creates one new invocation per completed trigger cycle. | One/all/any/until, partial selections and correlation; ordered data/envelope/raw input; terminator exclusion; repeated cycles and unrelated broadcast listeners; trusted principal scope. | C13, C14 |
| SCH04 | Claims and slot/invocation/input binding are atomic and recover the same handle. | Independent connections race; failure before binding, inside the transaction, after binding and before dispatch; original occurrence and fingerprint retained; stale completion cannot overwrite cancellation; actual PostgreSQL locking. | C01, C02, C03, C04, C11, C14, C15 |
| SCH05 | Admission, bounded operation and advertised support match actual execution. | Invalid types/read dependency/unsupported combinations rejected; embedded default and optional datasource; bounded batches and retention; API authorization/cancellation; exact capability and image assertions followed by frozen full suites and configured gates. | C08, C09, C12, C15, C16 |

## Test ownership and evidence boundaries

Shared acceptance is staged in `tests/contract/test_durable_timers.py`,
`test_durable_timer_process_recovery.py`, `test_durable_scheduling.py` and
`test_schedule_event_triggers.py`. Their current per-engine/backend case
counts are 13, five, eight and 14 respectively. The first timer slice
checks the common engine entry point, acknowledged waiting result, committed
timer fields, original workflow input, and release of resident execution and
its invocation ownership. A background bounded poller is distinct from a
sleeping task for each workflow wait. Clock-controlled tests stop that poller
before explicit delivery and advance its common absolute clock.

`test_durable_timer_forks.py` adds two cases per engine/backend for ordinary
and competitive parallel waits. These preserve the existing parallel-task
contract when a branch releases execution: runnable siblings are admitted,
each original timer remains durable, and a competitive winner exists only
after a branch completes. They cover C08/C16 integration rather than adding
a later milestone's fork semantics.

Core owns durable metadata, original due times, trusted scope, immutable
receipts and guarded claims. ADK and LangGraph own their checkpoint and native
suspension formats. Native qualification requires the actual independent SDK
environment; fallback success and PostgreSQL-unconfigured skips do not qualify
those profiles. Optional Agent Framework applicability is recorded separately
and does not create a production-image or native timer capability claim.

Bootstrap policy is `execution_timing`: `timezone` defaults to UTC and accepts
validated IANA zones, `max_pending_timers` defaults to 1000,
`max_concurrency` to 16, `schedule_batch_size` to 128 and
`poll_interval_seconds` to 0.05. These host controls do not add public DSL
properties. API schedule creation captures the authenticated middleware
owner/event scope and configured timezone. Payload principal or user fields
cannot authorize a different scope. Periodic delivery uses the saved nominal
slot clock; `after` successors use the immutable successful completion clock.

All fixtures use deterministic clocks/models/transports and no paid API. An
actual process-stop case must kill the native runtime PID after its committed
boundary, reopen in a different process, and verify the original identity,
input and known external outcome. Its environment is allowlisted and its
workspace is dedicated. Test lease durations do not change production policy.

Existing S2-03/S2-04 ownership, native/common receipt, deadline, retry schedule,
process recovery and operator lifecycle tests remain regression gates. S2-05
event/approval trust and backpressure remain required when schedule triggers
touch that boundary. Known corruption must fail admission before parent/native
mutation or external dispatch; tests do not repair missing evidence implicitly.

General compatible version migration remains S2-09, broader child lifecycle
S2-07, compensation/ambiguous external actions S2-08, external event transports
S4-10, and full proposed inventory/JavaScript qualification Stage 4. This record
does not add distributed scheduler, external broker, or universal exactly-once
requirements.

## Validation status

The shared scheduling run in the existing root environment passed
17 cases with 16 PostgreSQL-unconfigured skips. It exercised real common
invocations through both installed adapters, overlapping `every` runs,
successful `after` successors and fault/cancel termination, and reopened cron slots
for UTC, Asia/Dubai and both Europe/Berlin DST transitions. This is initial
shared evidence; it does not qualify independent native SDK or PostgreSQL
profiles. The exact log is `tmp/s2-06-scheduling-shared-cancel-cadence.log`.

The initial nonresident timer probe failed on the predecessor resident wait:
the invocation did not return within ten seconds for a 601-second wait. This
captures the required behavior missing before integration; it does not claim
that the later due-time assertion was reached. The retained negative log is
`tmp/s2-06-timer-nonresident-baseline.log`.

The integrated root timer focus passed 24 cases with 24 unconfigured
PostgreSQL skips before the final retry-attempt deadline case was added.
Its exact log is `tmp/s2-06-timer-shared-complete-focus.log`. That additional
case preserves the established initial-attempt behavior, then checks the
retry's original deadline both before and after expiry while its later wait
due remains unchanged. The first run exposed a concrete error-contract gap:
expired pending retry waits reached the catch block as runtime/500 rather
than timeout/408. After correction, the combined deadline-release regression
passed 11 cases with six PostgreSQL-unconfigured skips and 40 deselections
(`tmp/s2-06-deadline-release-regression.log`). The original failure is retained
in `tmp/s2-06-timer-retry-deadline-first-focus.log`.

Initial trigger semantics and transaction checks passed 24 cases with 24
PostgreSQL-unconfigured skips (`tmp/s2-06-trigger-shared-current-focus.log`).
Two later corruption cases verify the actual scheduled-dispatch entry point
rejects missing or changed completed-cycle evidence before invocation payload,
native state or model calls change; all four root SQLite nodes passed in
`tmp/s2-06-trigger-corruption-first-focus.log`. Early fixture corrections are
retained in the first-focus logs: the loop clock must advance after native
acknowledgement/owner release, and transaction failures surface through the
common sanitized error contract. These were fixture corrections and are not
claimed as production defects.

The configured PostgreSQL selectors now include 13 timer, two parallel-timer,
eight scheduling, 14 trigger and five actual process-stop cases for each
principal engine.
Collection guard success proves selection only. Actual PostgreSQL execution,
native execution and remote gate inspection remain separate acceptance steps.
The Windows native selection includes all four shared fixtures.
It also includes the parallel-timer fixture. The expanded selection guard
passed all 31 cases in 64.24 seconds
(`tmp/s2-06-postgres-expanded-selection-focus.log`); this proves actual case
collection without claiming execution against PostgreSQL.

The framework-neutral C02/C09 ownership regression is
`tests/core/test_timer_dispatcher_ownership.py`. It uses two actual
`RuntimeServices` instances and database connections, a real workflow and an
execution lease. A competing timer worker must resolve no dependencies,
dispatch no model calls and leave the invocation payload unchanged while
another owner holds that lease; after release it resolves once and completes
once. This SQLite guard is included in the common-store CI step and adds no
per-SDK or PostgreSQL recovery selector count. The guard passed in 0.75
seconds (`tmp/s2-06-timer-dispatcher-ownership-acceptance.log`). The native
LangGraph schema trace corroborates the boundary: the original external-input
schema recovery fixture passes with exactly one schema fetch and no competing-dispatch
refetch (`tmp/s2-06-schema-refetch-corrected.log`).

The combined current shared root focus passed 71 cases with 70 actual
PostgreSQL-unconfigured skips in 45.41 seconds
(`tmp/s2-06-shared-current-integrated-focus.log`). All 29 existing and expanded
PostgreSQL selector guards passed in 64.88 seconds
(`tmp/s2-06-postgres-full-selection-focus.log`). These runs qualify neither
an unavailable native SDK nor a skipped database profile.

Actual LangGraph SQLite qualification now includes all five forced process
stops. Each killed its ready runtime PID, observed no graceful cleanup, and
reopened in a distinct PID. Waiting preserves the original due time 1060 with
40 seconds remaining at 1020; wake/downstream receipts recover two distinct
HTTP operations once; claimed/bound slots preserve the original occurrence
with a newer owner generation, and an already bound invocation keeps its ID.
The retained first native log has five passing OS-stop nodes followed by one
legacy deadline observer failure:
`tmp/s2-06-lg-native-process-deadline-exact-first.log`. The observer was updated
to forward the new private `absolute_due` parameter without changing the
original deadline expectations. Its eight actual native deadline nodes then
passed, including workflow/task remaining 20/expired 0 and all four missing or
changed evidence controls
(`tmp/s2-06-lg-native-deadline-observer-corrected.log`). The first direct
interpreter launch lacked ephemeral pytest and executed no test; it is not
qualification evidence.

The same five forced-stop scenarios and eight original deadline guards passed
on actual ADK SQLite: 13 passed, 39 deselected, one SDK deprecation warning,
86.04 seconds (`tmp/s2-06-adk-native-process-deadline-first.log`). Selected
native cases had no skips. Both actual principal recovery slices have terminal
proof; actual PostgreSQL, frozen full suites and image/gate acceptance remain
pending. A skipped backend or a fallback run does not fill those remaining
gates.

Five actual process-stop scenarios are covered in
`tests/contract/test_durable_timer_process_recovery.py`: acknowledged waiting,
committed wake receipt before continuation, committed downstream HTTP receipt,
slot claim before binding, and committed slot binding before SDK dispatch.
They require an actual principal native SDK environment. Test workers use a
ten-second execution lease and a bounded recovery loop while separate expiry
and stale-owner guards retain their own contracts; production lease policy is
unchanged. Both principal SQLite executions are recorded above; actual
PostgreSQL execution remains pending.

Frozen full native suites, actual PostgreSQL execution, API/image reports and
final configured gates are pending. Each
terminal evidence entry will identify its source inputs, executed scope and
remaining limitations; a previous milestone's green job is not evidence of new
timer or slot-creation behavior. Prior failed attempts remain part of the
acceptance history.

Intermediate full suites remain diagnostic evidence, because corrections
were still in progress and their inputs were not frozen. Retained terminal
results are root 10 failed/2054 passed/1003 skipped, LangGraph 37 failed/1215
passed/746 skipped, ADK 37 failed/1172 passed/773 skipped, and optional Agent
Framework 15 failed/1522 passed/1071 skipped; each also had two expected gap
failures. Exact logs are `tmp/s2-06-root-full-integration-second.log`,
`tmp/s2-06-full-lg-integration.log`, `tmp/s2-06-full-adk-integration.log` and
`tmp/s2-06-af-native-full-first.log`. The observed failures included stale
resident-wait test assumptions and receipt-count assumptions after adding
timer registration, as well as actionable parallel-suspension and competing
timer-dispatch admission defects. Focused corrections do not replace the
required fresh frozen full suites.

The corrected lifecycle fixtures retain actual resume/cancel outcomes,
immutable HTTP operation IDs and complete event equivalence. Their root focus
passed all 19 cases (`tmp/s2-06-operator-lifecycle-clock-ack-focus.log`). The
actual ADK native focus then passed the legacy forced-kill waiting boundary
and all 19 operator cases: 20 passed, one unavailable counterpart native
process skip and 30 deselections in 26.38 seconds
(`tmp/s2-06-adk-legacy-lifecycle-focus.log`). Its 188 raw input hashes were
unchanged between start and finish. The corresponding LangGraph focus and
the new parallel-wait native cases are separate results. LangGraph's legacy
wait and operator focus passed 20 cases with one unavailable counterpart
native process skip and 30 deselections in 19.35 seconds
(`tmp/s2-06-langgraph-legacy-lifecycle-focus.log`); its 188 raw input hashes
were also unchanged. New parallel-wait native qualification remains pending.


## Frozen local validation and isolated harness corrections

The final local snapshot contains 274 hashed inputs. Root passes 2,073 tests,
1,007 unavailable-backend skips and two tracked integration expected failures
in 1,263.18 seconds, with 91.05% core coverage against the 90% gate. LangGraph
passes 1,256/750/two in 1,296.70 seconds; optional Agent Framework passes
1,543/1,077/two in 1,233.05 seconds. ADK records 1,212/777/two and one benchmark
failure in 1,370.55 seconds. All four supervisors finish within the finite
1,800-second local limit with every frozen raw hash unchanged. The GitHub
job and test deadlines are unchanged. The two expected failures remain the
OpenAPI parameter/server operation and HTTP binary output gaps owned by S3-03.
Unavailable native SDK and PostgreSQL skips do not qualify those profiles.

Exact logs are `tmp/s2-06-final-root-full.log`,
`tmp/s2-06-full-lg-final.log`, `tmp/s2-06-final-af-full.log` and
`tmp/s2-06-full-adk-final.log`, with corresponding terminal result/hash
manifests. These local results precede the two harness-only changes below;
configured full-suite results on the pushed source remain required.

The ADK benchmark failure is reproduced on its actual native SDK: the requested
50 ms wait completes during native receipt persistence, correctly returning a
completed invocation rather than a suspended one. The interruption probe now
uses a deterministic executor clock across reopen; its requested duration and
`perf_counter` latency measurements remain unchanged. Both actual native
engines pass the temporary corrected probe with one HTTP side effect and one
immutable operation ID. Production timer and ownership modules are unchanged.

TMR02/C01-C02 additionally requires direct PostgreSQL two-connection timer
claim, expiry, newer generation and stale-release proof. The existing fixture
is now parameterized for SQLite/PostgreSQL with isolated UUID keys and scoped
cleanup. The same claim assertions are preserved. It is already selected by
the configured common-store PostgreSQL step; no SDK recovery-selector count
changes. Local timer-store and benchmark focus passes 12 cases with one truthful
PostgreSQL-unconfigured skip. Actual PostgreSQL execution remains pending.

Current lint, formatting, strict runtime typing and audit/tracker consistency
checks pass. Both initial root/core wheels contain 54 matching top-level core modules
and pinned croniter 6.2.4/tzdata 2026.5; the schema bytes remain unchanged.
The post-freeze correction manifest records only `benchmarks/engines.py` and
`tests/core/test_timer_store.py` as deltas. This record does not close S2-06 or
publish a new capability before its configured acceptance gates.

The corrected tracked benchmark is subsequently verified on each actual
principal SDK at the configured 10 iterations/four concurrent invocations.
Both finish successfully, retain native checkpoint growth and recover with
exactly one HTTP call/immutable operation ID. The previously failing ADK
pytest node passes in 4.11 seconds. Evidence is
`tmp/s2-06-adk-benchmark-correction-result.json`,
`tmp/s2-06-adk-corrected-benchmark-test.log`, the ADK standard benchmark JSON,
`tmp/s2-06-lg-benchmark-tracked.json` and its terminal manifest. No input
changes occur during those checks; the only deltas from the frozen full
snapshot remain the two explicitly recorded harness files.


## First configured source and PostgreSQL corrections

Source `4cdf552fcfe1a48c66f52d9131684b71e3357ca2` is not a qualified configured
baseline. PostgreSQL run 38213965259 fails its common-store step: 251 pass,
two fail and one intentional SQLite variant skip. Both native recovery
stages and both PostgreSQL images are skipped. The new two-connection timer
claim test passes claim/expiry/generation/stale-release assertions, then
fails its due-key query because PostgreSQL cannot infer the untyped nullable
parameter. The optional invocation filter now uses a conditional WHERE clause;
scoped and unscoped query paths retain their original bounds.

The other actual database failure is schedule reopen at current epoch time:
legacy PostgreSQL REAL clocks use float4 while JSON evidence retains float8.
New schedule/slot timestamp columns use DOUBLE PRECISION. The private DDL
initializer explicitly widens existing float4 columns in a transaction while
preserving stored SQL values and payload bytes; it never reconstructs lost
precision from JSON. Already inconsistent rows remain rejected. New fixtures
cover epoch/fractional due and lease times, reopen, binding, strict changed
column rejection and isolated legacy layout/value preservation. Local focus
passes 21 cases with 17 unavailable PostgreSQL skips; fresh actual database
execution remains mandatory.

Both configured root jobs also fail two ordered-fork restart fixtures that
still expect cancellation of a resident sleeping task. Push root records
2 failed/2,075 passed/1,004 skipped/two tracked gaps; PR root has the same
counts and 91.06% coverage. The fixture now awaits acknowledged suspension
and verifies no active execution/task/lease plus original input. Its original
ordered branch and persisted terminal output assertions remain unchanged.
Both actual SDK environments pass the entire 18-case file; root fork/timer
store/dispatcher focus passes 29 cases with one unavailable PostgreSQL skip.

Both Security runs (38213965964/38213961619) pass. Each has six nonempty nested
reports with zero fixable or unfixable advisories: root 73, ADK 111, LangGraph
98, optional Agent Framework 76, Docker controller 13 and Kubernetes
controller 16 dependencies. Each runtime report includes the pinned croniter
and tzdata versions. External Sandbox run 38213965264 passes all four jobs.
These successful slices do not replace the failed root/database or skipped
main image gates. Raw failed logs, per-node outcomes and report hashes are
preserved under `tmp/ci-4cdf552-*`; corrected-source acceptance remains open.

The first optional Agent Framework run also fails: 1,541 pass, 1,077 skip,
two tracked gaps and two failing legacy ADK counterpart fixtures. Its fork
failure has the same corrected oracle; its shared-session recovery failure
comes from cancelling before ownership cleanup. The latter now awaits each
acknowledged suspended invocation, verifies released execution and exact saved
input, and retains distinct invocation IDs, a shared session and all reopened
output/input/model-call assertions. Actual ADK and LangGraph each pass seven
cases with 17 unavailable-profile skips. Optional Agent Framework passes
three applicable cases with 33 unavailable-profile skips; root passes two
with 22 skips. Existing native corruption/admission controls are preserved.

Combined corrected core scheduling/timer/dispatcher, shared schedule/trigger
and audit/tracker checks pass 77 cases with 62 unavailable PostgreSQL skips.
The corrected fork file also passes all 27 optional Agent Framework environment
cases. Current quality checks pass 252 formatted files, all lint, three CI and
79 strict runtime sources, and Markdown links. Both corrected root/core builds
are inspected recursively: all 64 core Python files match current source bytes
and both dependency pins are present. These newer distributions supersede the
initial pre-PostgreSQL-correction builds. Actual PostgreSQL, exact corrected
full CI and runtime images remain required; no failing source is qualified.


At corrected runtime source `3d2faf047aec0529926750d4897c345f20aef111`, actual
PostgreSQL common-store execution passes 255 cases, with one intentional SQLite
variant skip and one new fixture failure. The original wall-clock persistence,
both scoped/unscoped timer due-key queries after claim/reclaim, and fractional
epoch schedule reopen/binding cases pass. The failing legacy DDL fixture reads
REAL through abbreviated float4 text before widening but float8 afterwards;
Python sees 1791787000.0 before and the actual stored numeric value 1791787008.0
after. The value-preserving ALTER is correct. Both comparison projections now
cast clocks to SQL double precision, retaining every identity/payload/metadata
comparison and the lost-precision-not-reconstructed assertion. Production
modules and distributions are unchanged by this final test-only correction.
Local corrected schedule-store focus passes 16 cases with 17 unavailable
PostgreSQL skips. Native PostgreSQL and images were skipped by the failed
common step and remain unqualified until the fresh corrected-source run.
Security at `3d2faf0` passes all six fresh nonempty nested reports with zero
advisories and matching dependency pins; its raw evidence is retained beside
`tmp/ci-3d2faf0-pg-terminal-evidence.json`.


At `3dab25abb3c5017501685495c7eeaaa9346af75b`, the actual common-store step
passes, including all original fixes and the corrected legacy widening oracle.
The native LangGraph PostgreSQL recovery step records 342 passed and one
failing controlled-clock schedule case (1,005 deselected). Its exact two-call
assertion sees two additional `after` invocations left in the shared test
database, dispatched by an automatically started timer poller. Schedule-only fixtures now mirror the already isolated trigger fixtures:
they install the controlled executor clock before native initialization and
stop the poller immediately afterwards; their explicit scheduler owns delivery. No other fixture's durable rows
are removed and the exact model-call oracle remains unchanged. Actual ADK,
optional Agent Framework PostgreSQL recovery and images are skipped by this
failed step and remain unqualified.

The same source's optional Agent Framework full run records 1,542 passed,
1,077 skipped, two tracked gaps and one old null-checkpoint cancellation
assertion failure. A cancelled earlier CI stage also records that exact
checkpoint oracle failure; cancellation does not erase a completed failing
stage. A bounded sweep changes eight direct durable-wait setup functions in
seven fixture files to await actual suspension and released ownership. It
retains all null/JSON input matrices, output ordering, immutable HTTP keys,
child receipts, native input/scope corruption and no-repeat controls.
Real action/effect/retry gaps, event-listener cancellations and actual
forced-stop workers remain unchanged. Both actual native sweeps pass: ADK
84 cases/112 unavailable-profile skips and LangGraph 88/108; the additional
SDK/child-event slice passes eight/21 each. Optional Agent Framework's four
swept contract files pass 109 cases/171 unavailable-profile skips. No
production module changes occur in this fixture sweep. Exact hashes, fixture
classification and terminal native results are saved in
`tmp/s2-06-wait-oracle-sweep-evidence.json`.

Both current root jobs subsequently pass; the inspected PR root full records
2,078 passes/1,006 skips/two tracked gaps and 91.00% coverage, with its shared
repeat 1,006/868/two. External sandbox contracts pass 55 cases and both
external runtime image acceptance jobs pass. Fresh six-report Security
acceptance also passes. These independent positive slices do not qualify the
remaining database/optional/full principal/image gates or close S2-06.


The controlled schedule/trigger focus then passes 44 cases with 44 unavailable
PostgreSQL skips in root, actual ADK and actual LangGraph independently. All
188 recorded inputs remain unchanged during each run. These are positive
SQLite/native checks; fresh actual PostgreSQL remains required.

Completed native stages at `3dab25a` expose the already swept child-event
cancellation oracles plus a timing-worker ready/cleanup race. PR LangGraph
records three failed/1,157 passed/750 skipped/two tracked gaps; push ADK records
five/1,112/777/two. Counterparts cancel and main image jobs skip. Both Windows
jobs pass their first stage 466/12 and actual native stage 727/567. The always-run
artifact upload is not evidence of successful CTK/benchmark execution; the
failed/cancelled runs have no such artifacts.

The timing producer now awaits actual nonresident suspension and released
ownership, records the original timer identity/deadline/due time, publishes
readiness, then remains outside the workflow runner until the parent's actual
OS kill. Retry-delay's intentionally resident injected sleep is unchanged.
Recovery validates the original saved arming evidence and waits until that
same absolute deadline before requesting expiry recovery. Its timeout remains
four seconds; no lease-duration assumption or redrawn budget establishes
expiry. Six actual timing cases pass across ADK/LangGraph, preserving original
deadline equality, observation at/after it, remaining zero, zero model calls
and absent cleanup markers; both retry cases retain two calls/one jitter draw.

A proposed acknowledgment change in the historical legacy-scalar worker was
incorrect: that test intentionally runs archived producer source, whose waiting
runner remains resident. The retained intermediate focus times out on that
producer. Its original scalar/status observation is restored; an explicit
external stop barrier preserves its checkpoint until the OS kill without
requiring current-runtime nonresident behavior from archived code. The corrected
legacy case passes on each actual SDK, preserving old source/scalar evidence,
distinct producer/recovery PIDs, absent cleanup marker, current-runtime
`invalid_invocation_state` rejection, unchanged parent and zero calls.

Detailed raw JSONs and per-run hashes are retained in
`tmp/s2-06-final-worker-qualification-evidence.json`; failed intermediate
attempts remain separate. No parent-test or production-runtime assertion is
relaxed. All ten fixture/helper corrections are held for the next exact-source
configured full, PostgreSQL and image acceptance. S2-06 remains unchecked.


## Exact runtime validation at ec94b17 and event-worker isolation

Five configured runs at `ec94b17dc965b99aac237e960da475cb36baeb3d` succeed:
PR CI, Docs, Security, External Sandbox and optional Agent Framework. Actual
PR root full passes 2,078/1,006 skipped/two tracked gaps with 91.00% coverage;
shared repeat passes 1,006/868/two. Native LangGraph passes 1,160/750/two and
ADK 1,117/777/two; optional Agent Framework passes 1,543/1,077/two. Both main
runtime images complete their configured default/startup/restart/sandbox/catalog
steps. All four downloaded PR/push native benchmark JSONs match the source,
run 10 iterations/four concurrent invocations, report native checkpoint growth
and preserve one side effect/immutable operation after recovery.

The PostgreSQL run passes 256 core cases with one intentional skip and all
343 native LangGraph recovery cases. Native ADK records 308 passes and one
partial-all event process-stop failure: the helper's model counter is two,
not the required one. Its global automatic timer poller can consume unrelated
due invocations from preceding fixtures in the shared database. The worker's
model counter must cover only its explicit event invocation/recovery. Both
event and timing workers now suppress automatic timer startup before asynchronous
native storage initialization; current-runtime invoke/resume still uses all
normal common/native admission and receipts. The historical producer path is
unchanged. Prompt diagnostics are retained for test-only investigation; the
exact one/two-call assertions are not weakened and no shared rows are deleted.

All eight event/timing OS-stop cases pass on each actual SDK after isolation:
ADK eight passes/24 deselections in 86.46 seconds and LangGraph eight/24 in
70.69 seconds. Both 180-second supervisors finish normally with all 274 inputs
unchanged. Evidence is `tmp/s2-06-isolated-worker-qualification.json` and the
two full logs/result manifests. Fresh actual PostgreSQL remains required;
its optional Agent Framework and image stages were skipped by the ADK failure.

The push CI Windows job reaches its unchanged twenty-minute limit after its
first stage succeeds and 693 native-stage passes/567 skips. It is cancelled,
not green; the other push jobs, including both main images, pass. PR CI's
Windows run succeeds independently. No timeout or assertion is waived. The
corrected source will receive fresh configured validation before publication.
S2-06 remains unchecked.


At stable source `5e66f9940315d6a3e06a99cf15dee665bd329dcf`, six configured
runs pass: both main CI runs, Docs, Security, optional Agent Framework and
External Sandbox. Both main image pairs, full root/native/Windows suites and
benchmark evidence are reviewed. PostgreSQL core and LangGraph pass, but its
ADK step reports three uncertain-action timeout fixture failures and eventually
reaches the unchanged nine-minute deadline. The raw step is retained in
`tmp/ci-5e66f99-pg-failed.log`; no complete ADK summary or later database image
qualification is inferred from the timed-out output.

The retry-timing and durable-event modules exercise explicit invoke/resume
and selected event delivery while sharing database state. An opt-in test fixture
now suppresses only automatic timer startup for those two modules before native
initialization, preventing an unrelated fixture's due invocation from entering
their fake model or blocked action. Their original clock/random draws,
timeout-drainage, ownership, receipt/corruption and exact call assertions are
unchanged. Actual ADK and LangGraph entire-file focuses each pass 92 cases
with 116 unavailable-profile skips. Both 300-second supervisors finish normally
with all 275 inputs unchanged. Explicit timer dispatch, its forced-stop
fixtures and automatic CTK waits do not use that opt-in fixture. Automatic
Wait CTK focus still passes two cases in each native environment (94 deselected).
Fresh actual PostgreSQL and all corrected-source gates remain required.
