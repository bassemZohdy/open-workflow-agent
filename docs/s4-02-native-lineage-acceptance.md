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
existing coordinator replay tests still pass, including the SQLite crash cases. This is not
public qualification: crash and restart through the route, lifecycle gate installation, a
configured example and frozen-source qualification are still open, and S4-02 remains open.
