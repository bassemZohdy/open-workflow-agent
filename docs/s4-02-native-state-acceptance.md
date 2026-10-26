# S4-02 native state controls

Status: started after PR #85 (`2f33a3b`). State/history reads are qualified as a
bounded prerequisite for safe compare-and-update and branching. Native state
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
declared channels and changed-policy denial. Frozen platform/native/authority
qualification passes as recorded below.

Startup review also found that state registration needed to wait until configured
memory providers had opened, and that native-backend availability must be
evaluated after selecting the actual SQLite/PostgreSQL path. The corrected
initialization keeps the existing unsupported-loop rejection first, verifies
the selected backend, installs the reader inside guarded startup and removes
it on failure/shutdown. Capability metadata is emitted only while the matching
extension is advertised; withdrawing policy does not leave a readable endpoint.

## Frozen qualification — `f060349`

Runtime/tests are frozen at `f060349333908d2da3723cd57f19dbe5581ef3a3`.
The combined selection includes all 39 actual state/API cases, 20 core state
contract cases, native task/adapter, corruption, completed-scope/receipt,
authority/fencing, stream and selected configured-memory regression checks.
Windows and Docker/Linux use separate fresh PostgreSQL databases. SQLite stays
the embedded default; the native read path creates no database or namespace.

| Selection | Windows | Frozen Docker/Linux |
| --- | --- | --- |
| Combined native state/history and existing contracts | 285 passed, zero skips | 285 passed, zero skips |
| Separate actual ADK authority/fencing/stream/state rejection | 20 passed, zero skips | 20 passed, zero skips |
| SDK-free core/configuration/API/recovery/tracking | 312 passed, zero skips | Not selected |

Linux reuses immutable dependency image
`sha256:591b1bc4959940d8c6d7fa4f5c5b2471ddf2d68570e04415cbc420981f5e9f42`
after verifying unchanged root/LangGraph locks. ADK uses
`sha256:741a21400325548a51308cdbd6a5c9055443f7eecc8f950b43042c955d58be3b`
with matching root/ADK locks. Source and root filesystems are read-only; containers
run as a non-root user with dropped capabilities and bounded resources. No
packages are installed at startup. All 1,250 frozen Linux source inputs remain
unchanged. Manifests, logs and XML remain under
`tmp/merge-20261025/f060349/`. The combined selection finished in 909.08 seconds
on Windows and 281.43 seconds on Linux. Both deselect 84 opposite-engine cells;
the separate ADK selection covers its authority/fencing/rejection boundary.
This is component qualification, not full repository or release qualification.

## Remaining state-management work

After PR #86, the private updater prerequisite runs the pinned SDK's
`aupdate_state` inside one transaction containing its checkpoint, pending writes
and native proof. It compares the root head before dispatch, rejects changed
requests and returns verified retained proof on an identical repeat. SQLite
defers the SDK's individual commits until the fenced outer commit; PostgreSQL
uses one SDK cursor connection and an exclusive ownership-row lock. Both verify
ownership again before commit. The lease must exclude live workflow execution.
No runtime registration, setup call, API route or mutation capability is enabled.

Development checks cover rollback after SDK writes, changed scope/result,
concurrent compare-and-update, damaged proof/lineage, stale and expired owners,
repeated cancellation, and process termination before commit. PostgreSQL's
ordinary shared fence initially allowed two same-base updates; this private
unit now takes an exclusive lock without changing ordinary native execution's
fence contract. The first PostgreSQL fixture mistakenly selected a temporary
SQLite root; fixing fixture selection allowed the real PostgreSQL cases to run.
Earlier reports remain under `tmp/merge-20261025/state-atomic-dev*.xml`.
Frozen qualification of this private prerequisite is pending.

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
