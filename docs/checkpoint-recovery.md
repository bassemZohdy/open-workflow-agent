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
