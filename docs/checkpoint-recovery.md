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

## Evidence and remaining work

The verified private action journal foundation retains
a first-owner intent and the first completed value/null/fault outcome on the
common datasource, with stable private occurrence identity and a request digest.
Conditional writes reject another owner. An unresolved pending intent differs
from a completed null result. RuntimeServices composes this store on the common
datasource. All exact foundation gates passed at `d197fa1`, including the actual
PostgreSQL value/null/fault store fixtures. This storage acceptance does not
establish recovery leases or exactly-once actions.

The leaf-call integration follow-up is under verification. Protocol calls,
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
Executable run, event and child operations, recovery leases, and general
pending-outcome resolution remain open; no broader capability is advertised.

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
General pending effects, recovery ownership and missing/inconsistent native
checkpoints remain open in S2-03. Retry/deadline, durable event/timer, child
workflow, compensation/idempotency and migration retain S2-04 through S2-09.
Full proposed criteria, public native extensions and JavaScript qualification
retain their later-stage owners. The [full inventory](../resources/compatibility/feature-tracker.json)
remains the scope of delivery.
