# Native workflow data updates and recovery

The opt-in LangGraph source profile `langgraph.state.update@1.0.0` manages the
data of a waiting invocation. It uses the existing native checkpoint store,
common execution lease and immutable control receipts. SQLite is the embedded
default; PostgreSQL is optional. This source profile is not yet a published
image, and S4-02 remains open.

Mount the [bootstrap configuration](../examples/native-state-updates.yaml) and
[OWS workflow](../examples/native-state-updates-workflow.yaml) under `/config`,
provide `OWA_NATIVE_STATE_TOKEN` through deployment secrets and retain `/data`.
The example uses the fake model and makes no external model call. Workflow
behavior stays in OWS; authentication, authorization and limits stay in bootstrap
configuration. Both `native_state: {}` and `native_state_updates: {}` are explicit
opt-ins. Default installations and ADK reject required update workflows.

## Update and continue

Invoke the example with `POST /v1/invoke` and `{"input":{}}`. It sets value 1 and
waits. Use the returned invocation ID to read its current data and opaque base:

```http
GET /v1/invocations/{invocation_id}/state
Authorization: Bearer <deployment token>
```

Copy `items[0].reference` into the request below. `values` replaces the complete
root data object; it is not a JSON merge patch. The declared schema validates it.

```http
POST /v1/invocations/{invocation_id}/state-updates
Authorization: Bearer <deployment token>
Idempotency-Key: update-1
Content-Type: application/json

{"base_reference":"<opaque current reference>","task_reference":"/do/0/first","values":{"value":7}}
```

The response contains the common `operation_id`, exact `request_digest`, opaque
base/result references and `state: completed`. It contains no graph, thread,
checkpoint IDs or internal plan. A control does not resume the workflow. Continue
through the existing resume endpoint, retaining the changed data with null input:

```http
POST /v1/invocations/{invocation_id}/resume
Authorization: Bearer <deployment token>
Content-Type: application/json

{"input":null}
```

The example returns `{"value":8}` without repeating earlier tasks. Earlier task
history, variables, context, counters, routes and declared channels are preserved.
Using the same update key and identical request returns its verified retained
outcome, including after workflow completion. Changing its request conflicts.
Terminal invocations cannot admit new updates or new recovery decisions.

## Ownership and declared policy

The original authenticated owner needs both current and retained original
`state.update` permission on `invocation:{invocation_id}`. Reads use
`state.inspect`; resolution uses `state.resolve`. Session/user labels and opaque
references confer no authority. Cross-owner access returns not found.

The exact required update declaration must supply `allowedTaskRefs`,
`updateSchema` and `authorizationPolicyRef` under its `nativeExtensionData` entry.
Only declared, previously visited direct root set tasks may be attributed. The
base must still be the current root head. Inputs are finite bounded JSON objects,
and schema references must resolve offline. The native transaction compares the
exact base and resulting projection before committing checkpoint/writes/proof.
Common receipts bind the result and ordered prior control identities. Existing
checkpoint and command formats retain their original meanings.

The official OWS 1.0.3 schema is unchanged. Updates are separate from the
observational read profile; capabilities expose `features.nativeStateUpdates`
only while its configured installation is valid. Unsupported required versions
fail before API invocation creation.

## Interrupted controls

Inspect retained common metadata with:

```http
GET /v1/invocations/{invocation_id}/state-updates
Authorization: Bearer <deployment token>
```

The list is labeled `verification: common_metadata_only`; a stored phase alone
does not prove a native outcome. Update errors include the operation ID and
request digest when available. Repeating the original request can complete an
interrupted acknowledgement from matching native proof. A dispatched control
without proof is never blindly sent again.

An explicit resolution uses the listed operation ID and exact request digest:

```http
POST /v1/invocations/{invocation_id}/state-updates/{operation_id}/resolve
Authorization: Bearer <deployment token>
Idempotency-Key: resolution-1
Content-Type: application/json

{"request_digest":"<retained digest>","decision":"complete","acknowledge_unknown":false}
```

`complete` requires matching retained native proof. `abandon_unknown` instead
requires `acknowledge_unknown: true` and no current matching proof. Its audit and
the control outcome commit together. Neither choice repeats a native write.
Abandonment records uncertainty, claims neither rollback nor nonexecution, and
blocks further updates or adopting the native head for continuation. Later proof
does not silently change that decision. An identical committed resolution may
be acknowledged after workflow completion; a changed choice conflicts.

## Limits and lifecycle

`native_state_updates.bounds` uses the state-view history, native decoding,
response and timeout bounds. Defaults are two concurrent controls, a 30-second
deadline, 16 MiB of native data and 1 MiB responses. Response bounds must be at
least 1,024 bytes. The combined read and update logical packet budget is capped
at 256 MiB; this is not a hard process-memory or isolation guarantee.

There are at most 64 control intents per invocation. `max_workflow_bindings`
(default 64) additionally bounds simultaneously installed executable workflow
bindings. They are reference-counted and released after active operations;
ordinary inspection and restart/tree audit use a plan-free proof reader rather
than accumulating bindings. Full capacity returns HTTP 429 without queuing work.

Deadlines and repeated cancellation drain active native database work before
releasing its control slot or execution lease. Cleanup can exceed the nominal
deadline. A committed native write can remain awaiting common acknowledgement;
inspect or repeat the same request instead of choosing another key. Native
connection/statement/lock limits supplement the request deadline. Shutdown drains
the same work before unregistering the controller. Native proof storage is set up
only during explicitly configured startup, never during control dispatch.

Changing configuration or storage bindings fails closed. Replay, time travel,
bulk updates, other task/scope profiles and broader S4-02/T45/T49 acceptance remain
open. See the [acceptance record](s4-02-native-state-acceptance.md).
