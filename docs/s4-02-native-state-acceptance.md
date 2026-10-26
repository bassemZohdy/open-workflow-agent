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
Frozen runtime `3bf27d736510e741425e12e496d7925da6b62bae` selects 30 direct SDK
transaction cases, 39 configured workflow state/API cases, 20 core state cases,
eight native fencing cases and two LangGraph admission cases. Ten ADK cells
are deselected; this unregistered module changes no ADK behavior. The direct SDK
transaction fixtures do not qualify OWA workflow state mutation or continuation.

| Private updater selection | Windows | Frozen Docker/Linux |
| --- | --- | --- |
| Atomic SDK unit plus state-read/fencing/admission regressions | 99 passed, zero skips | 99 passed, zero skips |

Linux uses the same immutable LangGraph dependency image and deployment
restrictions as the read profile above, with verified unchanged dependency locks
and a separate fresh PostgreSQL database. All 1,253 source inputs remain
unchanged. Reports and the input manifest remain under
`tmp/merge-20261025/3bf27d7/`. The selection finished in 168.67 seconds on
Windows and 62.02 seconds on Linux. Ruff, formatting, strict typing of 161
core/LangGraph source files, relative Markdown links and four tracking checks
pass. This qualifies only the private storage primitive and retained regressions.

Build compare-and-update on exact base references, original-owner management
authorization, schema/allowed-task attribution and the common owned mutation
intent/outcome contract. Use the native updater under the shared fence; commit
its checkpoint/writes and proof atomically, and reconcile interruption without
blind redispatch. Do not reinterpret old versioned meanings or weaken native
receipt/definition validation.

### Private common control journal after PR #87

The private common coordinator now records bounded prepared/dispatched/completed
state-control intents and ordinary common effect receipts. Commands contain
opaque base references, JSON-only update values, task attribution and policy/
storage pins; core stores no native checkpoint IDs or checkpoint payloads.
Control identity stays fixed when attribution or values change, so those changes
conflict with the original idempotency slot. Both current caller authority and
retained original authority must allow `state.update` for the original owner.
Each permission receipt binds the exact request digest; a read-only authorization
inspection also rejects a self-consistent command rewrite that retains old
permission. The common and native per-invocation ceilings are both 64 updates.

The adapter bridge uses read-only inspection with bounded proof-record columns and opaque reference
translation. It resolves the archived definition and checks native input byte
bounds before the SDK updater. Common transactions end before native awaits.
Known native proof completes an interrupted common acknowledgement; a dispatched
intent without proof fails as unknown and cannot dispatch again. Ordinary effect
and invocation-index verification checks the control audit and native proof.
The backend must be explicitly installed; none is installed by default.

Focused checks cover actual SQLite/PostgreSQL commit/recovery, damaged or missing
audit/lineage, original-owner and policy rejection, prepared interruption,
repeated requests, cancellation and process termination before or after native
commit. The latter uses a later ownership generation and no duplicate SDK call.
Direct SDK graph fixtures prove this storage/recovery boundary; they do not prove
OWA update policy/schema/attribution, continuation or replay semantics.

Development reports are retained under `tmp/merge-20261025/state-journal-dev*.xml`
and `state-controls-dev*.xml`. Initial reopen fixtures used an unavailable
InvocationStore attribute, then supplied a libpq connection string where the
public datasource API requires a URI. They now retain the original configured
datasource. The temporary local test artifact was removed. A test attempted to
patch a slotted connection instance; it now patches the class with an
instance-specific guard. Frozen qualification is recorded below.

Public update admission, explicit policy/schema/allowed attribution, complete
workflow snapshot/receipt validation and integration, lineage-metadata byte
bounds, unknown-control resolution and safe continuation
still need implementation and qualification. No update route or capability is
enabled by this component, and S4-02/T45/T49 remain unchecked.

#### Frozen control-journal qualification — `7572808`

Runtime/tests are frozen at `7572808c72d2a6acf95a07f6a47e893187e16d38`.
The combined cohort selects the common state-control cases, actual SDK control
bridge/process-exit cases and private atomic updater cases together with state
reads, native fencing, execution-authority isolation and admission regressions.
Windows and Linux use separate fresh PostgreSQL databases. Linux uses the same
immutable LangGraph/ADK dependency images and read-only/non-root/resource
restrictions recorded above; dependency locks are verified before reuse.

| Selection | Windows | Frozen Docker/Linux |
| --- | --- | --- |
| Combined controls/state/authority/native regression | 174 passed, zero skips | 174 passed, zero skips |
| Separate actual ADK authority/fencing/rejection | 20 passed, zero skips | 20 passed, zero skips |
| SDK-free common journal/authorization/memory/recovery/API/configuration | 653 passed, zero skips | Not selected |

All 1,257 frozen Linux source inputs remain unchanged. Reports remain under
`tmp/merge-20261025/7572808/`. The first SDK-free command
stopped during collection because it selected `tests/core/test_api.py`; the
correct API selection is `tests/e2e/test_api.py`. The corrected run uses another
fresh database and `core-journal-final.*` reports. The original zero-case report
is retained and does not count as acceptance. The combined selection finished
in 311.42 seconds on Windows and 114.55 seconds on Linux, with 36 other-engine
cells deselected. The SDK-free selection finished in 197.84 seconds. Ruff,
formatting and strict typing of 168 core/ADK/LangGraph source files pass.
This is private control storage/recovery qualification, not public mutation,
full repository or release qualification.

### Declared update policy and OWA preflight after PR #88

Compilation now derives an immutable `NativeStateUpdatePolicy` from exact
required `langgraph.state.update@1.0.0` metadata and its `nativeExtensionData`.
The payload contains only `allowedTaskRefs`, `updateSchema` and
`authorizationPolicyRef`. One to 64 unique direct root set-task references map
to canonical OWA task references. Schema and policy data have a 65,536-byte
ceiling. JSON Schema 2020-12 validation uses offline reference closure, including
local references; external unresolved references and other dialects fail
compilation. Values must be bounded finite objects matching that schema and the
exact deployment policy ID. Channel metadata retains its existing contract and
can coexist with update policy. Legacy source fingerprints remain unchanged.

No engine advertises the mutation capability. Shared engine checks still reject
the exact and unknown required versions before creating an invocation. This
stage makes the policy and preflight executable; it does not enable mutation.

The adapter-private preflight requires a waiting original owner and live execution
lease. It checks current and retained original permission before native reads,
resolves the archived definition, validates attribution/schema/policy and reads
the exact current root head. Native record byte bounds precede SDK decoding.
The existing OWA native state/input/receipt and completed-scope validators inspect
all retained scope evidence through a cache bounded by scope count and aggregate
native bytes. Fresh physical definition pins and common receipts are rechecked
synchronously after native awaits, then owner/lease/storage bindings are checked
again. The immutable adapter-private result retains a base packet for a future
comparison inside the native transaction; it is never a public execution plan
or a core checkpoint store. No intent, checkpoint or model call is created.

Focused fixtures run ordinary OWA set/do/wait tasks through the private adapter
to obtain real suspended state, including a completed child scope. They explicitly
prove public engine admission is closed first. Their read-only preflight checks
cover current/stale bases, schema failures, unexecuted attribution, different
owners, changed common identity, missing/corrupt child scopes, byte/scope budgets
and timeout cleanup. Native lineage proof inspection also bounds parent metadata
before materializing it. The initial PostgreSQL corruption injection used a
literal percent wildcard where psycopg expects placeholders; it now binds only
the current invocation's thread. Reports remain under
`tmp/merge-20261025/state-policy-dev*.xml` and `state-preflight-dev*.xml`.
Frozen qualification is pending.

Public configured ingress, atomic updated OWA snapshot/receipt attestation,
interrupted-control resolution and safe continuation remain required before
advertising updates. Replay, time travel, broader native profiles and full
S4-02/T45/T49 retain their original unchecked scope.

Replay and time-travel branches require a new business identity and immutable
lineage, a pinned definition/checkpoint and explicit read-only/re-execution
effect policy. Bulk updates must retain native semantics and allowed task
attribution. Existing business history must remain unchanged. These gates are
part of the original S4-02 scope, not deferred tasks or claimed completions.
