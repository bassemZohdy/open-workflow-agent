# S4-02 native state controls

Status: started after PR #85 (`2f33a3b`). State/history reads are implemented as a
candidate prerequisite for safe compare-and-update and branching. Native state
updates, workflow replay and time travel remain open. S4-02, T45 and T49 are
unchecked; no research row is promoted by this component.

## Read/history contract and verification

The [configured source profile](native-state.md), [bootstrap example](../examples/native-state.yaml)
and [OWS workflow](../examples/native-state-workflow.yaml) select
`langgraph.state.read@1.0.0`. Core owns typed queries/results, permission and
definition/archive verification. The adapter owns root checkpoint selection,
native format/size guards and safe data projection. The native saver remains
the only checkpoint store; references are opaque derived aliases, not saved
copies of checkpoint data.

Original-owner `state.inspect` permission is required before any native I/O.
Current and retained original authority are checked. Exact required declarations
gate the feature; unsupported required versions fail before API invocation
creation. Default configuration serialization omits the new optional block.

SQLite read-only/query-only and PostgreSQL read-only repeatable-read snapshots
enforce consistency. The read path does not initialize a namespace, create a
database, run SDK setup DDL, acquire execution ownership, emit action receipts
or dispatch workflow tasks. Connection/statement/lock/progress deadlines, native
serialized byte/array guards, response bounds and a bounded reader pool apply.
Repeated cancellation drains the active database task before freeing its slot.

Current/history views validate native identity and state/input digests plus
immutable common receipts. Only task data, declared channel values, public root
visits and opaque lineage references cross the boundary. Every response says
continuation is not inspected; state views cannot authorize resume or replay.

The oldest checkpoint initially failed because execution admission correctly
forbids adopting an initializer after action evidence exists. Historical
inspection now uses a separate validator entry point. All existing identity,
input, binding and pending-write checks remain; only the historical view permits
later action evidence. A direct regression proves the original execution
admission method still rejects the same initializer.

The first privacy assertion matched the word `snapshot` in the verification
label rather than a private payload key; it now tests private field names.
An append-channel fixture initially supplied objects, which the existing
declared list contract correctly rejected; it now supplies list-valued writes.
Earlier development reports remain under `tmp/merge-20261025/state-*.xml`.

Focused state/API cases cover SQLite/PostgreSQL, root/history pagination,
cross-owner selectors, explicit permission, pre-decoding byte bounds,
read-only database enforcement, absent storage, history-window bounds,
initializer admission separation, repeated cancellation, fresh-process reads,
declared channels and changed-policy denial. Final frozen platform/native/
authority qualification remains pending.

## Remaining state-management work

Build compare-and-update on exact base references, original-owner management
authorization, schema/allowed-task attribution and the common owned mutation
intent/outcome contract. Use the native updater under the shared fence; commit
its checkpoint/writes and proof atomically, and reconcile interruption without
blind redispatch. Do not reinterpret old versioned meanings or weaken native
receipt/definition validation.

Replay and time-travel branches require a new business identity and immutable
lineage, a pinned definition/checkpoint and explicit read-only/re-execution
effect policy. Bulk updates must retain native semantics and allowed task
attribution. Existing business history must remain unchanged. These gates are
part of the original S4-02 scope, not deferred tasks or claimed completions.
