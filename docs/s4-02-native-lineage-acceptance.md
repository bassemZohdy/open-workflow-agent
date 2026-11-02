# S4-02 selected-checkpoint lineage preparation

Updated 2026-10-27. S4-02 and T49 remain open. The preparation component validates
the basis for a future native replay or branch. A separate private transaction
unit now writes selected scopes under a pre-reserved target identity. Neither
component creates or admits a public child invocation, dispatches effects,
exposes an HTTP route or advertises a replay/branch capability. Both principal engines reject required
replay/branch profiles before invocation creation.

## Declared policy and common command

The compiler accepts exact versioned metadata for `langgraph.state.replay@1.0.0`
or `langgraph.state.branch@1.0.0` only with its required declaration. Each policy
has an `authorizationPolicyRef` and its respective `replayEffectPolicy` or
`branchEffectPolicy`, exactly `read_only` or `reexecute`. Branching also requires
the existing bounded update policy with the same authorization reference.
Missing, optional, unknown-version, malformed or extra policy data is rejected.
The official OWS 1.0.3 schema is unchanged.

The immutable common request pins distinct source/target business identities,
workflow fingerprint, opaque selected checkpoint reference, source basis,
storage binding, declared policy and effect policy. Re-execution requires an
explicit acknowledgement; read-only requests reject that acknowledgement.
Branch values and attribution use the existing root update schema/task policy.
Replay cannot silently supply new values. A fixed source/request control identity
and content digest distinguish duplicate requests from conflicting commands.
Common descriptors contain no native checkpoint IDs, native state or plans.

## Read-only native basis

Preparation requires both the current request and retained original authority
for `state.replay` or `state.branch`, the original principal and archived workflow
identity. The target must be distinct and absent. It acquires no source lease,
creates no intent or target invocation and changes no source ownership generation.

Bounded native inspection locates the selected historical root by its opaque
reference, including an earlier initializer. It validates the original input,
OWA projection, common receipts, checkpoint parent and native metadata. It follows
only the transitive completed-scope closure required by that root, verifies each
required digest and completion, and excludes scopes completed by later progress.
Fresh physical/archive/common validation follows native reads. Current authority,
target absence and storage binding are checked again before returning.

The immutable internal basis pins the exact checkpoint coordinates, metadata,
input and OWA projections under native byte/scope/deadline limits. Its digest
includes the private checkpoint ID without placing it in common descriptors.
Future forks must not inherit SDK pending writes; this preparation records their
presence but stages only the validated OWA projection. No from-start restart is
substituted for selected-checkpoint replay.

## Development evidence

Focused SQLite/PostgreSQL checks cover historical initializer/current/earlier
roots, completed scope closure and exclusion of later scopes, altered basis,
missing required scope, invalid target/reference/schema/attribution, resource
limits, absent/different/revoked authority, detached values and explicit
re-execution acknowledgement with zero dispatch/model calls. Compiler/command
checks cover exact declarations, immutable policies, strict descriptors and
unchanged legacy workflow fingerprints.

The first native development run retained eight failures: the inspection probe
needed the scope executor's coordinates, and test enumeration closed a saver
before fully consuming its SDK generator. Both were corrected. Subsequent
targeted checks pass six cases; the complete new native preflight plus existing
update preflight selection passes 56 cases. The two explicit re-execution cases
also pass. Compiler/workflow/admission regression passes 129 cases. Reports
remain in `tmp/merge-20261025/lineage-*`; frozen cross-platform qualification
follows separately. No task checkbox or research row is promoted by these checks.

## Remaining implementation and acceptance

Development work on the current branch adds bounded native proof inspection and
a private root-only replay projection. A target checkpoint carries a lineage
binding and source-backed receipt coordinates, and continuation with that
binding installs the common effect gate. Actual SQLite and PostgreSQL replay
tests cover historical receipt selection, read-only denial, acknowledged
re-execution, repeated continuation, missing lineage context and damaged native
proof. A focused run passes 16 cases with no skips. The test constructs and
binds its target explicitly; it does not exercise the normal invocation lifecycle
or prove restart recovery. An earlier combined run had one recovery-profile
failure after a runtime file changed during testing; a fresh database and stable
source passed. Broader regression and frozen cross-platform qualification remain.

On the development branch, native resume reconstructs a registry from retained
common and native proof before ownership, then reinstalls the scoped gate when
continuing the target under ownership. Four restart-style tests use a newly
initialized engine and common services on SQLite and PostgreSQL. Both effect
policies are checked, including repeated continuation. These tests manually
stage the target and call the internal owned continuation; they do not prove
public admission, crash recovery or durable target creation. Read-only registry
inspection can run without a lease, but binding or authorizing an effect still
requires target ownership. Two PostgreSQL runs were interrupted when the WSL
test container exited; the passing run used a fresh Docker Desktop database.
The common registry and reserved-identity selection passes 30 cases; a native
proof, receipt-history and admission regression passes 20 cases, both with
SQLite and PostgreSQL and no skips. Formatting, lint, strict typing, task
tracking and Markdown-link checks also pass for this development state.

The current branch adds a private reservation in common storage: the target,
its original input and the request binding commit together. Repeated requests
validate the target identity and input, and a newly initialized runtime can
recover the request and re-read the selected native basis. A six-case selection
passes on SQLite/PostgreSQL for transaction interruption, changed reservation,
restart-style continuation and recognition of an already committed native fork.
This still manually coordinates the stages in the tests; it does not establish
process-crash reconciliation or a public replay API. A simultaneous regression
run had one failure because two suites counted and changed shared PostgreSQL
receipt tables concurrently; its result is retained rather than counted green.
The same preflight and admission selection subsequently passes 40 cases when
run serially against that database, with no skips.

The private root replay coordinator now reconciles the reservation, original
authority admission, native fork, lineage binding and owned continuation. A
14-case SQLite/PostgreSQL selection passes with no skips, including externally
terminating an independent worker after reservation, admission, fork and
binding, then resuming the same target. Both read-only and re-execution policies
are covered, including repeated calls. A separate two-case SQLite/PostgreSQL
selection terminates a worker inside a model call; continuation faults without
another model call. An initial PostgreSQL attempt lost the deliberately short
test lease before reaching that call; a longer test lease passed on both stores.
These tests do not qualify a public replay API or broader replay profiles.
The associated proof, reservation, receipt-history and admission regression
passes 22 cases across SQLite and PostgreSQL, with no skips.
An earlier expanded run was interrupted after an observed failure; the apparent
PostgreSQL case passed independently, but the interrupted run retained no failure
details and is not green evidence. The subsequent complete selection is recorded
in `tmp/merge-20261025/lineage-coordinator-crash-dev2.xml`.

The private root coordinator now accepts a contiguous completed-task prefix
of the root scope, not only the first task: the target resumes after the last
completed task and re-executes only the remaining ones. A new SQLite test
(`tests/langgraph/test_native_replay_later_checkpoint.py`) replays a three-task
workflow from the checkpoint after the first and after the second task, checks
that only the remaining model calls repeat, and checks that a repeated
coordinator call repeats nothing. The existing replay integration selection
still passes on SQLite (12 passed, 4 PostgreSQL cases skipped). PostgreSQL was
not run for this change, and nested scopes, native channels, timing and
Command/Send remain excluded by the staging profile. This is private and is not
public admission.

The private fenced SDK fork unit below precedes durable common
lineage/idempotency and recovery, checkpoint/scope binding remapping, branch data
attribution and effect enforcement before exposing controls. Prove read-only
effects cannot execute, explicit re-execution creates new receipts, interruption
and restart do not duplicate work, and parent history/authority remain intact.
Public admission, lifecycle budgets, configured examples, full T49 and broader
S4-02/time-travel qualification remain required. Broader memory/stream/native
scope gates and [state-control acceptance](s4-02-native-state-acceptance.md) remain
independent.


## Frozen source qualification — `f018d7f`

Runtime/tests/examples are frozen at
`f018d7f17256c41c41445fb76d046e1fbdb8b254`. The combined cohort adds 32
compiler/command cases, 32 actual selected-basis cases, two explicit
re-execution acknowledgement cases and four LangGraph admission cases to the
previous 420-case state-control/native/authority regression. All required
SQLite/PostgreSQL cases run; other-engine cells are deselected explicitly.

| Selection | Windows | Frozen Docker/Linux |
| --- | --- | --- |
| Combined lineage/state controls/native/authority regression | 490 passed, zero skips | 490 passed, zero skips |
| Separate actual ADK authority/fencing/rejection | 26 passed, zero skips | 26 passed, zero skips |
| SDK-free common compiler/journal/authorization/recovery/API/configuration | 881 passed, zero skips | Not selected |

Windows and Linux use separate fresh PostgreSQL databases. Existing immutable
SDK dependency images retain matching locks. Linux runs without package
installation, with read-only source/root filesystems, a non-root user, dropped
capabilities and resource limits. All 1,286 frozen Linux inputs remain unchanged.
Logs, XML, manifests and machine-readable results remain under
`tmp/merge-20261025/f018d7f/`. The combined cohort finishes in 792.60 seconds
on Windows and 320.62 seconds on Linux; the SDK-free cohort takes 270.85 seconds.
Formatting, Ruff, strict typing of 180 core/ADK/LangGraph source files, Markdown
links, task tracking and whitespace checks pass. Final documentation changes
leave frozen runtime/test/example inputs unchanged.

This is source qualification for private policy and read-only basis preparation.
It does not qualify native fork execution, effect enforcement, public replay or
branch controls, published images, full repository/release acceptance or task
completion. Task counts remain 31/37 verified, six open, none deferred. The
conditional S4-09/S4-10/S4-11 tracks remain conditional.


## Private atomic SDK fork unit — 2026-10-27

The native writer consumes an immutable selected basis and explicitly staged
OWA projections for an already reserved target. A trusted future coordinator
must authorize/reserve that target and retain its exclusive lease. The callback
may perform only SDK storage work; it receives detached fresh target configs,
without source checkpoint IDs. It must not run OWA tasks, models, tools or common
persistence while native/common ownership locks are held.

The writer verifies the exact database binding pinned by preflight before opening
a native connection. The transaction revalidates each source checkpoint/projection, parent and
metadata under raw byte guards before SDK decoding. All target scopes must be
absent. SDK writes for the complete declared scope closure and the native
`owa_state_lineage_v1` marker commit together, or all roll back. Target ownership
is locked across the unit; repeated cancellation drains it before the caller
can release ownership. SQLite reuses the existing deferred SDK commit/fence
boundary with a separate proof table; PostgreSQL also serializes the finite
per-source quota across different targets.

Proof pins source/target/fingerprint, exact common command and source basis,
staged projection digest and ordered target checkpoint coordinates. Repeats
require a live target fence and matching retained native projections/parents;
they return proof without SDK redispatch. Conflicting requests, changed staged
values, a different control using an existing target, missing/damaged checkpoints
and oversized proof fail closed. Maximums are 64 scopes, 64 fork records per
source, a 65,536-byte proof, and configured native byte/history/deadline bounds.
The writer performs no setup DDL; its proof table must be prepared separately.
Source pending task writes are never inherited into the new target configs.

This establishes storage atomicity only. Staged projections still need common
receipt/scope binding remapping and durable lineage admission before they are
safe to resume. Tests manually reserve target handles; the native writer does
not create them. A native marker alone is not a public replay, a completed
business fork or an exactly-once effect guarantee. No capability, HTTP route,
production image or task/research status is promoted.

The first development cohort passed 22 transaction cases but retained six fixture
teardown errors after deliberately expiring leases. Cleanup now tolerates that
specific expected ownership conflict. Expanded fork and existing updater checks
then pass 70 cases across SQLite/PostgreSQL. Quota/concurrency checks and frozen
cross-platform regression follow separately; reports remain under
`tmp/merge-20261025/lineage-fork-*`.


The first frozen candidate `81691b7` was superseded during qualification when
review found its low-level writer needed an explicit preflight storage-binding
check. Only the affected LangGraph runs were stopped; their partial logs and
cancellation note are retained and are not green evidence. The new check rejects
an altered binding before opening native storage; its four focused binding/
atomic-scope witnesses pass. Independent core/ADK selections use unchanged
runtime/test inputs and continue without being restarted for this native-only
correction. Final native qualification uses a new frozen source and fresh stores.


## Frozen native fork qualification — `479906c`

Native runtime/tests/examples are frozen at
`479906c7b0915f216c3411eac6d7211cd2cdc330`. The combined selection adds all 48
actual fork cases to the previous 490-case policy/state-control/native/authority
regression. The new cases cover complete scope closure, new target checkpoints,
source preservation, exact repeat without redispatch, rollback after partial/all
SDK writes, invalid scope/results/values, concurrent duplicate requests, expired
ownership, cancellation draining, corrupt/oversized proof, conflicting requests,
pending-write exclusion, packet/scope bounds, per-source quota, orphan target
writes and preflight storage-binding rejection before native access.

| Selection | Windows | Frozen Docker/Linux | Source |
| --- | --- | --- | --- |
| Combined fork/lineage/state/native/authority regression | 538 passed, zero skips | 538 passed, zero skips | `479906c` |
| Separate actual ADK authority/fencing/rejection | 26 passed, zero skips | 26 passed, zero skips | `81691b7`, unchanged inputs |
| SDK-free common compiler/journal/authorization/recovery/API/configuration | 881 passed, zero skips | Not selected | `81691b7`, unchanged inputs |

The core/ADK cohorts completed independently of the native-only correction.
Git comparison confirms unchanged core/ADK runtime, selected test/fixture and
dependency inputs. Only the affected native cohorts were restarted; the first
candidate's partial native results remain explicitly superseded. The final
native runs use fresh, separate PostgreSQL stores and the existing immutable
SDK image with matching locks. Linux retains read-only source/root filesystems,
non-root execution, dropped capabilities and resource limits, without package
installation. All 1,288 frozen Linux inputs remain unchanged.

The final native selection finishes in 837.01 seconds on Windows and 450.09
seconds on Linux, with 42 other-engine cells deselected. Core takes 294.36
seconds. Manifests, XML, logs and result JSON remain under
`tmp/merge-20261025/479906c/` and the unchanged companion source under
`tmp/merge-20261025/81691b7/`. Formatting, Ruff, strict typing of 181
core/ADK/LangGraph files, Markdown links, four task-tracking checks and diff
checks pass. Final documentation changes no frozen runtime/test/example inputs.

This qualifies the private native transaction unit only. Common target creation,
lineage admission/audit/recovery, receipt/scope remapping, effect enforcement,
public configured controls, broad T49 and full S4-02/release qualification remain
open. Task counts stay 31/37 verified, six open, none deferred, with the three
conditional tracks unchanged.


## Common lineage and scoped effect gate — 2026-10-29

A private common registry binds a reserved target to a typed lineage request,
caller-supplied native proof verifier and up to 64 references to verified source
business receipts. It records the binding through the existing fenced common
receipt journal, verifies both original/current authority and the retained
request-bound authorization decision, and rejects conflicting or orphaned
bindings. Reopening the common stores re-verifies the proof and source receipts.
The native verifier is a required injected boundary; these tests use a fake and
there is not yet an installed native proof inspector or lineage coordinator.

Receipt remapping represents the target identity and its immutable source
receipt dependency without fabricating a target business receipt. The target
identity has the same workflow task reference and occurrence but a new invocation
identity. Policy/internal receipts are not blindly remapped. Existing source
receipt verification runs before recording and on every read; missing or altered
evidence fails closed.

A trusted continuation may explicitly enter the lineage scope. Within that
scope, the common action-authorization boundary verifies the retained binding,
native proof, current target ownership and effect identity before permitting an
action. `read_only` denies external actions; `reexecute` requires its recorded
explicit acknowledgement and still passes through ordinary target authorization
and new receipt creation. Unscoped ordinary execution is unchanged. The scope is
not yet installed by the engine's continuation path, and receipt mappings are
not yet consumed by native checkpoint validation. Therefore this is a private
component, not end-to-end replay enforcement or a public capability.

Focused development checks passed 50 compiler/policy/registry cases on SQLite and
PostgreSQL; an earlier wider authorization selection passed 238 cases. Initial
runs exposed a tuple-to-JSON conversion error and an ordering error in the proof
check; both were corrected and the final focused selection is green. Neither
result completes S4-02/T49 or changes a research status. Native proof inspection, common target reservation/idempotent recovery,
verification that mapped receipts belong to the selected checkpoint,
checkpoint/scope binding remapping, global recovery verification, mandatory scope
installation and actual replay/branch continuation remain required before
admission.


## Common lineage qualification — `6532346`

Runtime/tests/examples are frozen at
`6532346dfb2d625fc4292aab62da1205dc3b4c36`. The combined selection runs 22
SQLite/PostgreSQL common lineage cases and 32 policy cases alongside actual
LangGraph authority isolation, checkpoint fencing and required-capability
rejection. A separate ADK selection covers its authority/fencing/rejection
contracts. No paid APIs or production images are used.

| Selection | Windows | Frozen Docker/Linux |
| --- | --- | --- |
| Common lineage, policy and LangGraph contracts | 91 passed, one PostgreSQL setup error; that case passed on fresh-store retry | 92 passed, zero skips |
| Separate ADK authority/fencing/rejection | 25 passed, one PostgreSQL connection failure; that case passed on fresh-store retry | 26 passed, zero skips |

Both original Windows failures reported the PostgreSQL server closing the host
connection during service setup. A subsequent direct connection succeeded and
both failed cases passed serially with fresh databases. The original failures,
retry XML, logs and result JSON are retained under `tmp/merge-20261025/6532346/`.
Windows evidence is the original run plus targeted retries, not a single green
full run. The Linux selections use a read-only source/root filesystem, non-root
user, dropped capabilities, bounded resources, existing SDK images with matching
locks and no startup installation. All 1,290 frozen Linux inputs remained
unchanged. The combined runs took 137.84 seconds on Windows and 46.46 seconds
on Linux; the separate ADK runs took 95.47 and 28.82 seconds respectively.

Formatting, Ruff, strict typing of 182 core/ADK/LangGraph files, Markdown links,
four tracking checks and whitespace checks pass. Final documentation does not
change frozen runtime/test/example inputs. This remains a private component:
no end-to-end replay capability, native proof inspection or task completion is
claimed. The backlog remains 31/37 verified, six open and none deferred; the
three conditional tracks remain conditional.

## Public replay route (local development result, 2026-11-01)

The private coordinator is now reachable through the opt-in `native_state_replay`
profile and `POST /v1/invocations/{id}/state-replays` ([guide](native-state-replay.md)).
A local run of `tests/langgraph/test_state_replay_api.py` passes all 32 cases (SQLite
and PostgreSQL 16 in a throwaway container) for both effect policies, including the
per-source limit (also under concurrent distinct requests) and the replay depth rule. The
existing coordinator replay tests still pass, including the SQLite crash cases. The
configuration-only example in `examples/` is also exercised end to end (34 cases in total
on both backends). A process stop at each coordinator stage, driven through the route by a
subprocess running the real app, recovers by repeating the identical request on SQLite and
PostgreSQL (12 cases run; 8 skipped by design because read-only interruption is only
meaningful at the bound boundary). Cancelling a running replay target through the public
route also holds on both backends (the target ends cancelled, performs no late work and is
not resurrected by a repeated request). Graceful shutdown mid-continuation leaves the target
running and a restarted runtime recovers it to faulted without repeating the call, and a
changed execution policy refuses a new replay before anything is created (both on both
backends). Revoking authority during a running replay fails the target closed
(`execution_policy_changed` at the in-flight task, no later effect). This is not public
qualification of S4-02: the replay profile's hosted Linux qualification is recorded
[separately](validation-history/s4-02-public-replay-hosted-qualification.md), and S4-02 remains
open.

## Later root checkpoint database coverage — 2026-11-02

The private three-task root replay test now runs against SQLite and PostgreSQL,
including selection after either the first or second completed task, remaining
model calls and idempotent repeated coordination. On Windows, both SQLite cases
passed and a separate selector-event-loop run passed both PostgreSQL cases against
an isolated PostgreSQL 16 container (103.92 seconds). An initial default-loop
run failed both PostgreSQL cases before replay because the driver requires a
selector loop. A subsequent combined selector-loop run recorded a PostgreSQL
failure but was interrupted before its traceback was retained; it is not green
evidence. The focused PostgreSQL rerun passed both cases. The existing hosted
PostgreSQL job already selects this test; hosted results for the expanded cases
are not yet recorded. This does not qualify broader replay or complete S4-02.

## Concurrent replay request binding — 2026-11-02

The public controller now checks the source, selected reference and effect
acknowledgement before a request joins an in-flight request with the same key.
A different reference fails with the existing reservation conflict rather
than receiving the first request's target, both before and after binding.
Identical requests retain their existing shared-flight behavior. This check
does not change the durable reservation or the supported replay profile.

An HTTP regression reproduced the previous 202 response for a conflicting
in-flight reference. The corrected focused regression passes eight cases on
Windows with SQLite and PostgreSQL 16: both effect policies and both binding
phases. An expanded selection initially had 19 passes and one failure because
the new test's preliminary PostgreSQL history read timed out; the revised test
uses distinct well-formed references with the coordinator held, removing that
unnecessary read. The original failure is not counted green. The separate
authorization/checkpoint contract selection had eight passes and 16 skips for
unavailable backend/engine cells. This is local evidence, not hosted or full
S4-02 qualification.

## Unsupported replay basis admission — 2026-11-02

The root replay coordinator now validates the inspected source profile,
completed-task prefix and source receipt dependencies before reserving a target.
It repeats that validation while staging under ownership. Unsupported historical
bases therefore fail before consuming an invocation, an idempotency key or a
per-source reservation slot; this does not relax the supported replay profile.

An HTTP test reproduced the prior defect on SQLite: rejection of an initial
checkpoint left one new invocation and one reservation. After the change, a
12-case Windows selection passes on SQLite and PostgreSQL 16, covering initial
checkpoint rejection followed by reuse of the same key, later completed-task
checkpoints, and real coordinator continuation under both effect policies. A
separate two-case selection verifies an unsupported nested/timed source creates
neither a target nor a reservation on both stores. A separate SQLite process-stop
case passes for recovery after reservation under the re-execution policy. The applicable authorization
and native checkpoint contract selection passes eight cases with 16 skipped
other-backend/engine cells. An initial test setup failed before exercising replay
because its reservation table was not initialized; the fixture was corrected.
These are local results, not hosted qualification or completion of S4-02.

## Private root branch projection — 2026-11-02

An unadvertised staging function validates the existing branch and update
policies and prepares one root checkpoint with replacement data, a new workflow
identity and remapped source receipt dependencies. Attribution must be the last
completed root task; a different visited task or altered native node is rejected.
The replacement is revalidated against the declared schema, and the declared
effect policy must match the request. The existing fork writer and common lineage
gate remain responsible for native proof and effects. This function does not
reserve, admit or dispatch a branch and does not provide a public API.

Manually staged tests execute a real native fork and continuation, including an
earlier model receipt, source checkpoint preservation, repeated continuation,
read-only denial and acknowledged re-execution of a later model call. A fresh
Windows selection passes 12 cases across SQLite and PostgreSQL 16: eight branch
cases and four later-checkpoint replay cases. The applicable authorization and
checkpoint contract selection passes eight cases with 16 other-engine/backend
skips. An earlier combined run had 15 passes and one PostgreSQL replay failure;
an isolated trace showed its private test had bypassed creation of the engine
namespace before writing native checkpoints. The corrected private tests create
the namespace before direct native execution; the earlier run is not green
evidence. An initial branch fixture also failed schema admission because it
declared a call task as an update attribution; the corrected fixture uses set
tasks and retains a separate prior model call.
Tracking and PostgreSQL CI-selection checks pass 65 cases; the added branch
test is selected by the existing hosted PostgreSQL replay job.

At this projection stage, branch reservation and lifecycle admission had not
been implemented. Broader attribution and scopes, public authorization and
hosted qualification remain open. These local component checks do not complete
S4-02 or T49.

## Private branch reservation — 2026-11-02

The common reservation transaction now accepts an explicitly authorized private
branch after validating the selected root profile and declared branch/update
policies. Its shared identity boundary rejects reuse across replay and branch;
per-source counts remain separate by kind. The original input, immutable request,
target identity and digest commit atomically. Identical retries and a fresh service
can recover and re-inspect the original request; changed values, altered attribution,
wrong kind and interrupted creation fail without adopting a different target.
The existing replay depth check conservatively treats any lineage target as a
replay target; this does not enable branch replay or a public branch endpoint.

On Windows, a fresh SQLite/PostgreSQL 16 selection passes 20 cases, including
actual process termination after branch reservation on both stores, fresh-service
request reconstruction, native fork/continuation, replay reservation regression,
and per-source limits. A separate authorization and checkpoint contract selection
passes eight applicable cases with 16 other-engine/backend skips. The process-stop
witness covers the reservation boundary only, not interruption during fork,
binding or continuation. At this reservation stage a branch coordinator had not
yet been implemented. Public branch admission, broader attribution/scopes,
hosted qualification and full S4-02/T49 remain open.

## Private branch coordinator — 2026-11-02

A private coordinator reuses the common lineage stages to reserve, admit, fork,
bind and continue an attributed root branch. Exact retries recover the same
target; changed data is rejected. When a lineage binding already exists, recovery
verifies the retained request and native proof before continuing instead of
reapplying the initial fork. This matters after continuation has written pending
state to the original fork checkpoint. The original fork writer's initial-state
checks remain unchanged, and uncertain effects still fail closed.

Local process-stop tests cover re-execution after reservation, admission, native
fork, binding and inside a model call, plus read-only recovery after binding, on
SQLite and PostgreSQL. A stopped in-flight model call faults without a second
call. The original combined Windows run had 12 passes, two failures and two
intentional read-only skips: one PostgreSQL preflight exceeded its test's default
five-second bound and one test-only manual ownership lease expired during the
long interruption loop. The affected cases were rerun on a fresh PostgreSQL
database after using the supported 30-second preflight bound and releasing the
manual lease before the loop: both passed. The separate SQLite five-stage case
passed after an initial short worker lease failure and a later discovered
fork-proof recovery failure; both failed attempts remain non-green evidence.
The authorization/checkpoint selection passes eight cases with 16 other-engine
or unavailable-backend skips. These are separate local runs, not one green
cross-platform qualification.

At the private coordinator stage, public admission had not been implemented.
Broader branch attribution and scopes, hosted qualification and the remaining
S4-02/T49 criteria are still open.

## Public root branch source profile — 2026-11-02

The optional `native_state_branch` installation exposes an original-owner branch
route for the existing attributed root profile. It requires authenticated state
views and state updates, checks both current and retained authority, validates
the declared update schema and effect acknowledgement, and uses distinct
idempotency keys, bounded active work, per-source limits and depth-one lineage.
The source is unchanged; the new target continues under the original authority.
The installation snapshots its configuration, includes branch settings in the
execution-policy identity, and bounds the combined state-view, update, replay
and branch decoding budgets. Active branches cannot exceed their native
concurrency bound. The [operator guide](native-state-branch.md) and
configuration-only example describe the finite profile.

Local Windows selections pass 29 HTTP, limit, configuration and example cases
across SQLite/PostgreSQL; six SQLite and six PostgreSQL route-level process-stop
cases pass in separate runs. The SQLite cases cover reservation, admission,
fork, binding and an in-flight model call with re-execution, plus read-only
binding recovery. The PostgreSQL selection covers the same stop points. An
in-flight uncertain model call faults without a repeat. Engine-neutral route and
wire checks pass five cases; applicable LangGraph authorization, checkpoint and
lineage-policy contracts pass 40 with 16 other-engine/backend skips. Two actual
ADK required-branch rejection cases pass. Core branch/replay and startup checks
pass 45 cases. A separate SQLite/PostgreSQL revocation selection faults an
in-flight branch when its original execution policy changes. These are separate
local runs; they are not a single green hosted qualification or a release/image claim.

Retained development attempts include one PostgreSQL history-read timeout in an
earlier 23-pass/one-failure HTTP run, a cancelled partial run when review found
that active branch concurrency exceeded the memory-budget calculation, and an
11-pass/one-failure SQLite run whose policy-mutation fixture attempted an invalid
configuration. The final profile uses the supported 30-second view bound for its
test fixture, accounts for active work in the combined budget, and tests a valid
policy mutation. None of those superseded attempts is counted green. The dedicated
hosted PostgreSQL branch job later passed for the frozen source identified below. Broader
scope/attribution, time travel, independent JavaScript parity and full S4-02/T49
remain open.

A separate branch-revocation and replay regression selection had nine passes and
one PostgreSQL replay history-read timeout under the replay fixture's five-second
view bound. The affected replay case passed alone on a fresh PostgreSQL database;
the original run remains a failure, not a green combined result. Both branch
revocation cases passed in the original selection.

Hosted PostgreSQL qualification was dispatched for frozen source
`43a0ffca8d52a0c5cc5660af767fbc90f69c12c5` on the dedicated
`codex/s4-02-branch-qualification-43a0ffc` branch:
[workflow run 41375652540](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/41375652540).
The dedicated PostgreSQL branch and replay jobs passed. The overall run still had
other jobs in progress when checked; no full-workflow result is claimed.

## Lineage-bound target state views — 2026-11-02

The original owner can inspect retained branch and replay target state through
the existing bounded state views. Projection reconstructs the durable lineage
verifier without acquiring ownership or changing the target, checks native
proof, retained authorization and receipt bindings, and shares the selected
checkpoint's native decoding budget. Missing or inconsistent proof fails closed.
Lineage depth and the existing replay/branch admission restrictions are unchanged.

On local Windows, a combined SQLite/PostgreSQL selection passed eight branch
proof-change/proof-removal cases and three replay-owner cases; its last PostgreSQL
re-execution view timed out under the replay fixture's default five-second limit.
That combined run therefore failed (11 passed, one failed). The affected case
passed separately on a fresh PostgreSQL database with a supported 30-second view
limit. This is separate local evidence, not a green combined or hosted result.
The initial owner-state regression reproduced a 409 before the change and passed
afterward. The frozen hosted run above covers its earlier source, not these views.
Two additional local state-reader/branch-projection regression selections were
interrupted after they stopped making visible progress; neither is counted green.
A subsequent focused SQLite state-reader selection passed four cases, with four
PostgreSQL cases skipped because that separate run had no configured datasource.
The SQLite re-execution branch-projection case passed separately with diagnostic
authorization tracing in 154 seconds. It includes five process-stop stages and
intentional lease-expiry waits, explaining its long periods without test output.
