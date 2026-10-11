# Checkpoint recovery work

S2-03 is in progress. Native checkpoints remain private to each adapter; common
invocation records retain public identity, workflow fingerprint and terminal
outcomes. Neither record type implies exactly-once external actions.

## Original input and resume input

The first execution binds its raw JSON input to the common invocation and
workflow fingerprint in a private `invocation_inputs` table. Later envelope
replay reads that binding instead of replacing workflow input with the resume
message. JSON null is a stored value. Independent connections cannot replace
the first binding. Invalid JSON, corrupt saved data and a different fingerprint
fail through common errors without including stored input in error messages.

Resume input is delivered separately through the common lifecycle control. A
submitted null wakes a wait, as do false, zero, empty string, array or object.
Workflow input transformation still runs against the original raw input, and
`$workflow.input` retains that raw value. Native LangGraph restores its original
input and task state from its private saver. ADK replay receives the common raw
input binding. The optional Agent Framework envelope follows common behavior;
these fixtures do not establish optional native checkpoint hardening.

Older invocations without a raw-input record have no newly qualified migration
guarantee. Compatible version migration remains S2-09.

## Terminal records and adapter finalization

The common executor writes terminal status together with output or structured
error in one invocation update. It commits that update before publishing common
completion and before the native adapter finishes. A failed SQLite store commit
rolls back the candidate and leaves the in-memory handle unchanged.

After that terminal update, interrupted finalization or a late adapter result
must preserve the committed common outcome. A fresh service returns the saved
value, null or fault without executing the completed invocation again. Late
native resume completion or failure likewise preserves committed cancellation.

The ADK resume callback tracks completion separately from its returned JSON
value. A successful null result therefore does not request a second portable
execution. If the SDK does not call the callback, the existing envelope recovery
path remains; general pending side-effect reconciliation is still required.

## Conditional common state writes

Common invocation writes compare the exact loaded snapshot and a private
generation. A return from running through waiting to running invalidates old
snapshots. A stale writer cannot replace a newer record; repeated terminal
writes retain the first output/error, including successful null. Failed SQLite
commits leave both the durable record and local snapshot unchanged. The
generation stays in common storage and is absent from public invocation and
A2A projections. This is common metadata bookkeeping, not a native checkpoint.

Late native completion or failure reloads a committed terminal outcome instead
of overwriting it. An active-state conflict stops the old executor; it cannot
adopt the newer nonterminal state to manufacture a fault or enter ordinary
task/catch retries. Competitive branches propagate the conflict without a
winner. A conflict during action dispatch leaves its intent unresolved.
Loaded operator handles coordinate with the original in-process handle, and a
terminal commit during operator refresh wins without another transition.

[Store writer fixtures](../tests/core/test_invocation_store_writes.py) cover
independent/simultaneous writers, repeated states, terminal null/error retention,
immutable identity, legacy metadata and sanitized corruption errors.
[Shared worker fixtures](../tests/contract/test_invocation_writes.py) execute
late outcomes, loaded operator handles and conflict routing through independent
SDK environments. Their PostgreSQL equivalents are wired into the same
configured gate and passed at `6954db8`.

These guards do not exclude competing executions, renew a durable execution
lease or atomically fence native checkpoint writes. Exclusive ownership and
native consistency remain S2-03 work.

## Initial native recovery admission

Fresh recovery reads an existing common original-input binding. It cannot
create a missing binding from a resume message. Before accepting a status
transition, principal adapters require an ADK session event for the private
native invocation or a LangGraph root checkpoint, with original input matching
the common record. Missing/deleted native records, missing/changed input and
unavailable installed native persistence reject admission without execution or
a common-state write. Native storage/cleanup failures use sanitized common errors.

ADK sessions may contain multiple invocations. Each invocation's bound input
drives execution; session-wide state cannot substitute the first invocation's
input for another invocation. The native SDK remains responsible for its events
and checkpoint format. Shared-session restart fixtures verify both results.

[Admission fixtures](../tests/contract/test_recovery_admission.py) exercise the
actual principal SDKs and have the same configured PostgreSQL assertions.
The optional Agent Framework common bridge explicitly skips native admission
qualification. Full local suites passed at `58516fb`; fresh GitHub and configured
database acceptance subsequently passed all seven gates at `b9a2b31`.
These checks validate initial input bindings. They do not validate every nested
checkpoint field, atomically reconcile common/native state, fence native writes
or establish exclusive execution ownership. Those remain S2-03 requirements.

## Evidence and remaining work

The [forced process fixtures](../tests/contract/test_process_crash_recovery.py)
at `68722d3` kill an owned OS runtime worker without graceful cleanup and recover
in a distinct process. Eight SQLite cases passed independently in each principal
SDK environment and in the optional common bridge: native wait with null resume,
value/null/fault receipts before native task commit, an unresolved receipt after
fake external dispatch, and terminal value/null/fault before native finalization.
The fake external store has no deduplication; the unchanged call count verifies
that recovery does not repeat an action. Original input, first terminal results,
duplicate results and existing action records are preserved. The optional bridge
does not qualify Agent Framework native checkpoint durability. Configured
PostgreSQL and Linux/Windows gates passed at `ef29f23`. This selected
boundary proof does not establish exclusive recovery ownership, native write
fences, complete nested consistency or resolution of ambiguous external effects.

The [private ownership store](../core/src/open_workflow_agent/ownership.py) at
`75fbc0d` atomically claims, renews and releases expiring common records using
the database clock. Generation tokens survive release/reacquisition, and stale
owners cannot verify, renew or release a successor's record. Corrupt infinite
expiry fails closed. [Store fixtures](../tests/core/test_execution_ownership.py)
cover independent/simultaneous connections, expiry/reopening, identity rebinding,
forged tokens, host-clock skew, invalid durations and failed SQLite commits.
Local focused proof passed 25 cases with 12 unconfigured PostgreSQL skips;
matching configured cases subsequently pass at `cdd6e17`. That storage-only
source did not guard execution admission or native writes; its two-runtime
negative probe is retained as historical evidence.

At `b2dfc5c`, invocation/resume admission acquires private expiring ownership,
coalesces local callers and supervises renewal, loss and shutdown. Common writes
and actual SDK database mutations lock the ownership row through commit.
SQLite TEMP triggers and PostgreSQL guards preserve native checkpoint SQL and
serialization. Cancellation drains native storage workers before closing
connections or releasing authority. The shared runtime and actual native
writer fixtures prove stale writes cannot cross a takeover, on SQLite and
configured PostgreSQL; Windows fixtures also pass.

At `b2dfc5c`, the full PostgreSQL gate and both PostgreSQL images pass. Both main CI image
restart gates fail immediate resume with HTTP 409 while a forcibly killed
owner's lease is still live. Their root, Windows, native and CTK jobs pass.
Expiry-aware image acceptance waits for the real production lease and rejects
other errors. This correction and action intent/fork progress commit guards pass
all seven exact `2acf632` gates, including both main CI forced-stop images and
configured PostgreSQL. Truthful outcomes from the original dispatch owner
remain recordable after expiry, without authorizing another action. The shared
[authority fixtures](../tests/contract/test_action_execution_authority.py) and
[transaction fixtures](../tests/core/test_action_progress_ownership.py) verify
these distinct cases. Prior failed image runs remain historical evidence.

At `556da47`, LangGraph validates actual SDK-owned root and stored nested
snapshots before common resume admission. Finite JSON, duplicate keys, required
field/control types, plan/task/scope identities and self occurrences, execution
paths, workflow input/definition and context binding are checked before live
state changes. Two prior actual root/nested corruption probes fail; the
[consistency fixture](../tests/contract/test_native_state_consistency.py) now
passes 24 SQLite cases, including clean recovery. Full local native/shared/CTK
regression passes 809, with 254 skips and three known gaps. Fresh configured
PostgreSQL corruption qualification fails at `fc54600` because the fixture
targets blob rows while the SDK inlines primitive channels in checkpoint JSONB.
Its six other gates, including both main CI forced-stop images, pass. At
`005ab5b`, the fixture targets the actual inline field and re-reads the exact
checkpoint to prove mutation. SQLite recheck passes 24 cases with 24 database
skips; fresh configured acceptance is pending. This validates selected stored
fields; it does not reconcile all well-typed data with action receipts or prove
that every declared child cursor must have a checkpoint. Cancelled children can
legitimately lack a first checkpoint.

The [ADK event fixture](../tests/contract/test_adk_native_event_consistency.py)
reproduces acceptance of a contradictory later native input event on the prior
first-event validator. At `005ab5b`, all input events for the specific native
invocation must match its common binding and have unambiguous finite JSON.
Matching replay messages and unrelated session invocations remain valid.
The focus passes 12 cases (22 skips); full ADK native/shared/CTK regression
passes 780 (276 skips, three gaps). All seven exact `4058895` gates pass,
including 127 configured LangGraph and 108 ADK recovery cases, both PostgreSQL
images and both main CI forced-stop images. Historical fixture failures retain
their source scope.

At `e61c6a5`, stop/exit/end flags must agree with saved task routing. Native task
completion routes immediately to END when workflow end is already set. Six
prior Boolean-control probes fail; the focused suite passes 41 (37 skips),
and full LangGraph native/shared/CTK regression passes 815 (270 skips, three
gaps). Routing acceptance and its separate ADK benchmark failure are recorded
at `6af95cf`; later receipt/native-reader source passed all gates at `c276f40`.

Historical probes at `e61c6a5` demonstrate a native/common receipt gap. After the first
fake HTTP call is native-completed and the workflow waits, deleting or changing
its common receipt still lets recovery complete and dispatch the next call.
The observed history is `/once`, `/next`; the first call is not repeated, but
known receipt disagreement is accepted. Recovery must verify checkpoint
dependencies on existing common receipts before accepting an invocation change.

Source `9755e96` stores immutable common receipt dependencies inside native
state. Both principal SDKs reject missing, changed or pending receipts before
another action or common invocation update. Identical root/nested value/null/fault
fixtures pass. ADK stores sequenced receipt/wait events and drains SQLite reads
through its existing native storage worker; a later sibling receipt preserves an
existing wait. All seven exact `c276f40` gates pass, including 159 LangGraph/134
ADK configured recovery cases and both actual runtime images in both main CI runs.

Source `968ec8f` also binds completed LangGraph scope checkpoints to later
native progress. Deleted or internally consistent changed children reject
admission; earlier loop occurrences remain required. A cancelled child with an
entered cursor but no first checkpoint remains valid. Local native/shared/CTK
passes 850/357/three tracked gaps. At `ac478e1`, all seven jobs succeed, including
Windows and actual image restarts, but its PostgreSQL keyword filter deselects
the nine new scope cases. Those successful jobs do not qualify PostgreSQL scope
corruption recovery. Source `3df61f6` corrects the real selector and verifies the
selected cases through actual pytest collection. Fresh configured acceptance
is required. Native formats/SQL remain engine-owned; binding version 2 introduces
no older-checkpoint migration guarantee.

The common sandbox executor at `3df61f6` extends action receipts to existing
deployment-enabled script, shell and container operations. It records an intent
before dispatch and a known value/fault outcome before native task progress.
Replay retains known outcomes, blocks changed requests and refuses to redispatch
an uncertain intent. Execution identity is stable per private occurrence;
explicit retry attempts remain distinct. Both principal focused suites pass
49 checks with 36 local PostgreSQL skips, including existing execution/lifecycle
parity. All dispatch still uses `SandboxManager`; policy and capabilities are
unchanged. The receipt fixture uses deterministic fakes, so it establishes neither
stronger isolation nor real-cluster acceptance. Fresh full/configured acceptance
is recorded in [PROJECT.md](../PROJECT.md).

The verified private action journal foundation retains
a first-owner intent and the first completed value/null/fault outcome on the
common datasource, with stable private occurrence identity and a request digest.
Conditional writes reject another owner. An unresolved pending intent differs
from a completed null result. RuntimeServices composes this store on the common
datasource. All exact foundation gates passed at `d197fa1`, including the actual
PostgreSQL value/null/fault store fixtures. This storage acceptance does not
establish recovery leases or exactly-once actions.

The selected leaf-call integration passed all exact gates at `fc53574`. Protocol calls,
catalog leaves and prepared imported calls save an intent before dispatch and
save a value/null/fault receipt before returning to native task execution.
Replay reuses the first outcome when the prepared request digest matches.
Imported/reusable scope wrappers still execute their task pipeline; their leaf
calls own receipts with private occurrence identity. Output transformation and
export run against the reused raw result.

A pending action without an outcome is uncertain. Recovery faults instead of
dispatching it again, adopting the earlier owner or entering task/catch retry
routing. Changed requests and failed receipt writes likewise require recovery
reconciliation. Competitive forks propagate these failures and drain siblings
without recording a winner. When restart occurs before any action intent, a
new race can still choose a winner. Public operation keys remain unchanged;
general repeated-action idempotency and compensation remain S2-08.

[Action recovery fixtures](../tests/contract/test_effect_recovery.py) interrupt
after leaf completion before native task commit, and after an external commit
before an outcome is available. They cover values/null/faults, changed requests,
duplicate terminal resume, repeated reusable calls, pending actions inside
retry/competitive scopes, and failed receipt writes. SQLite and configured
PostgreSQL share the same assertions in independent SDK environments.
Executable run receipts and execution ownership have subsequent evidence above.
Event/child operation receipts and general ambiguous-outcome resolution remain
open; no broader capability is advertised.

[Journal fixtures](../tests/core/test_effect_journal.py) cover owner contention,
reopening, first outcomes, source/occurrence separation, common errors, corrupt
receipts and failed SQLite commit. PostgreSQL equivalents are part of the
configured store gate. Native checkpoint state is not stored in this journal.

[Shared fixtures](../tests/contract/test_checkpoint_recovery.py) cover original
null/false/object/array inputs, all six empty/false JSON resume values, input
transformation, duplicate terminal resume, successful null completion, and
interruption/late fault/late value after common execution. Late cancellation
probes inject a deterministic native boundary. Independent native environments
exercise the other recovery cases through the actual SDKs.

[Store fixtures](../tests/core/test_invocation_inputs.py) cover separate
connections, reopening, finite JSON, fingerprint separation, corrupt data,
closed storage and failed SQLite commit. The same shared recovery matrix and
[PostgreSQL store fixtures](../tests/core/test_postgres_persistence.py) are wired
into the configured database gate for both principal adapters and the optional
common Agent Framework bridge. Local database skips are not database acceptance.

See [current validation](../PROJECT.md#s2-03-checkpoint-recovery--2026-10-11-in-progress).
General pending effects and complete missing/inconsistent native
checkpoints remain open in S2-03. Retry/deadline, durable event/timer, child
workflow, compensation/idempotency and migration retain S2-04 through S2-09.
Full proposed criteria, public native extensions and JavaScript qualification
retain their later-stage owners. The [full inventory](../resources/compatibility/feature-tracker.json)
remains the scope of delivery.
