# S4-02 native memory — private stores, repeatable reads and fenced transactions

Status: started after the main integration at `f013a2e`; S4-02 remains open.
This is a private component in the LangGraph adapter, not an enabled workflow
feature. A required `langgraph.memory@1.0.0` declaration still fails admission
before invocation/native mutation. No official schema, public API, native
extension payload or core memory contract changes in this slice.

## Implemented boundary

`NativeMemoryReader` receives an already installed SDK `BaseStore` and a fixed
deployment-side collection. Each `get` requires an active common invocation
action, resolves the original principal namespace through common services and
uses their `memory.search` authorization decision before touching the store.
It does not derive identity from input, user/session labels or mutable ambient
authority. It does not cache one principal's namespace for another invocation.
The native collection uses its own namespace; checkpoint/thread/session IDs
and existing common memory rows are not copied into it.

The reader calls the pinned SDK's `aget(..., refresh_ttl=False)`. It verifies
the returned record belongs to the exact requested key and namespace, then uses
the common finite JSON codec and returns a fresh value object. SDK Items,
namespace metadata and timestamps are not exposed. Record keys and collection
identifiers have fixed finite syntax. Projection defaults to 65,536 UTF-8 bytes,
with the common depth, node, finite-number and safe-integer restrictions; a
deployment-side typed bound may lower that limit.

The asynchronous read deadline defaults to five seconds and is capped at 30.
Backend failures become a sanitized common tool error; ordinary task cancellation
propagates. The deadline cancels the awaiting call, not necessarily a provider's
internal batching worker or database operation. The byte check applies after
the SDK has loaded the record; it is not a hard provider/process memory limit.
The reader starts no process and installs nothing. Provider setup is a separate
private deployment-side component, described below.

## Private provider and durable read profile

`open_native_memory_provider` uses the installed SQLite extra by default or the
deployment's optional PostgreSQL datasource. It checks the memory deployment
flag, typed provider binding, dependency and Windows event-loop support before
creating storage. SQLite uses `langgraph-memory.sqlite3`; PostgreSQL uses
`owa_langgraph_memory`. Both are separate from native checkpoints and common
memory rows. No semantic index, embedding service or TTL worker is configured.
Setup and close errors are sanitized; asynchronous SDK setup/close have bounded
deadlines and caller errors retain their original identity during cleanup.
No public provider selector or workflow registration is added.

Factory readers require a fixed installed provider ID, version and backend.
Common `MemoryReadObservations` retains the first committed JSON value or null
for a logical action occurrence, collection and key. It stores the descriptor,
bounded value and digest together with the effect identity index and completed
receipt in one common transaction. Read admission and commit require the live
original execution lease. The provider is awaited outside that transaction;
ownership loss during the read prevents its result from being committed or
returned. Concurrent reads use the first committed result.

Repeated reads, including recovery after process exit, return a copied retained
value instead of silently reading changed provider data. Provider binding or
record-size bound drift for the same logical read fails before provider access.
Original principal namespace and common authorization are checked before each
read, including cached reads. Changing the action occurrence permits a new
observation. An invocation can retain at most 64 observations; concurrent pure
provider reads can exceed that count, but additional observations cannot commit.
The component does not promise exactly-once provider dispatch. A crash before
commit can repeat the side-effect-free provider read; a crash or observer error
after commit retains the first observation even if the caller never received it.

The native receipt observer receives the common observation receipt. Common
invocation recovery reconciles both directions between observations and effect
indexes, and verifies their descriptors, values and digests. Missing or damaged
evidence fails recovery. This detects inconsistent stored evidence; it is not a
claim of protection against coherently rewriting all durable state. Observation
retention/deletion policy remains unimplemented. The next section describes
the private native write transaction foundation; it is not yet a workflow API.

## Private native mutation transaction

Core `MemoryMutation` defines a copied finite JSON request, original principal
namespace, installed binding, logical occurrence/key and immutable request
digest. Its slot excludes the value and binding so changing either for an
already committed logical write is rejected. Put and delete have distinct slots.
The original running common invocation, principal binding and lease token must
match before the SDK worker starts.

The adapter's private `apply_native_mutation` remains a trusted, preauthorized
primitive. It is not registered as a catalog function or agent tool. A later
private writer adds common authorization and the intent/completion protocol
described below; public workflow bindings are still disabled.
Provider setup creates a separate `owa_memory_mutations_v1` native marker table.
Each worker-owned SDK connection commits one SDK put/delete and its native marker
in the same native database transaction. The original pinned SDK batch SQL is
reused, with semantic indexing and TTL disabled.

SQLite owns the transaction and explicitly rolls back on every exception. This
avoids the pinned SDK cursor's commit-on-exception behavior. The existing common
SQLite ownership fence installs connection-local triggers on both the SDK store
and marker tables; it locks the attached common ownership row through commit.
The marker also fences deletion of an absent item. A final fenced marker update
checks lease expiry after SDK SQL. PostgreSQL uses one native transaction,
the common ownership row lock, invocation-scoped advisory serialization for
deduplication/quota checks, and a second lease check before commit. Connection,
statement and lock waits use the finite backend limits; this is not a hard
wall-clock bound under arbitrary network or operating-system failure.

Retries with a matching native marker skip SDK mutation SQL. They therefore
cannot restore an old value after another invocation updates the record, or
delete a record recreated after an earlier delete. A changed descriptor or
damaged marker fails before mutation. An invocation can retain at most 64 native
mutation markers; concurrent final-slot requests are serialized. Cancellation
drains the worker transaction before returning or allowing ownership/connection
cleanup; a committed write can therefore outlive caller cancellation and is
recognized on retry.

Native markers are not common effect completion receipts. The follow-up below
reconciles them with separate common intents and completion dependencies. The
low-level primitive must remain private; use the coordinator for the combined
protocol. Public bindings, write/namespace retention and remaining qualification
are still required. No native memory capability is enabled here.

## Common write recovery follow-up

`MemoryWrites` retains a bounded canonical request, fixed provider location
identity and common effect index/receipt in the common store. Intent admission,
dispatch transition and completion each require the live original execution
lease. Native database work is awaited outside common SQL transactions.

| Common phase | Native evidence | Recovery behavior |
| --- | --- | --- |
| Prepared | Absent | Eligible for its first dispatch. |
| Prepared | Present | Reject orphan or inconsistent evidence. |
| Dispatched | Matching marker | Complete the common receipt without repeating SDK SQL. |
| Dispatched | Absent | Report an uncertain outcome; never blindly redispatch. |
| Completed | Matching marker | Verify and reuse the completed acknowledgement. |
| Completed | Missing or damaged | Reject before checkpoint replay or another mutation. |

An interrupted dispatched operation with no evidence requires an explicit
resolution protocol before it can safely proceed; no automatic resolver is
implemented in this slice. Even a failure before the native transaction may
remain uncertain once common dispatch was recorded. This conservative profile
does not claim every pending write can recover automatically.

Common completion updates its phase and effect receipt atomically after native
proof, and checks ownership again before commit. Its receipt is sent to the
native action observer only after commit. Local duplicate calls share a weakly
held per-operation lock; independent recovery still relies on durable phase and
ownership guards. Native evidence and common indexes/receipts are reconciled in
both directions. Deleting either a common record or its native proof cannot
silently manufacture a replacement acknowledgement. Both common intents and
native markers have a 64-per-invocation ceiling.

Provider setup registers a fixed backend and location digest. Read-only native
marker lookup initializes no file, schema or SDK state and loads bounded marker
fields, with finite connection/query waits. Location identity excludes password
fields; no datasource string or credentials enter the workflow descriptor.
Changing the installed location rejects recovery of its old intents. Recovery
requires that original backend to be registered before invocation verification.

The private provider's `writer(collection)` accepts only a record key and finite
JSON object for put, or a key for delete. It checks original persisted principal
authority and `memory.add`/`memory.delete` policy before admission or reuse of a
common intent. Workflow input cannot select namespace, provider or database.
The writer returns no SDK identifier or marker. It is available to trusted
deployment-side functions; no public catalog entry or native feature declaration
is enabled. Cross-backend ownership storage is rejected for writes.

## Verification scope

The focused tests use actual pinned LangGraph stores (in-memory and optional
SQLite) and actual native workflow calls with common persisted authority.
They cover concurrent original-principal isolation despite identical caller
labels, copied JSON results, denial and deployment-disable before store access,
sanitized errors, asynchronous deadlines, invalid data/limits and the closed
required-feature gate. The SQLite case enables read-refresh by default and
checks the underlying store rows, including expiry, remain unchanged.

The Windows and frozen Linux actual-SDK selections each pass **41 cases with
zero skips**: the new reader tests plus existing native memory/checkpoint
admission regressions. Source `2f05979c1c0db9fec3fe31c9e33be99fc49686b7`
uses an immutable cached SDK dependency image with matching locks, a read-only
source/root filesystem, no network, a bounded writable temporary directory,
arbitrary UID, 1 GiB memory and 128-process cgroup ceiling. All **1,172 frozen
source inputs remain unchanged**; nothing is installed at startup. These are
test-container controls, not native memory capability claims.

The existing shared original-authority/concurrent-principal contract also
passes on actual LangGraph/SQLite. Ruff, formatting, strict adapter typing and
relative Markdown links pass. Raw reports are under `tmp/merge-20261025/`.
Those earlier reader checks did not establish a public memory API, durable
memory operations or full native store parity.

### Durable read/provider follow-up — `0268ea1`

Source `0268ea1ca9f8fb4a63ee7f68b7b221f702c09dea` passes **118 cases with zero
skips on each of Windows and frozen Linux**. The selection comprises:

- `tests/core/test_memory_read_observations.py`: actual SQLite/PostgreSQL
  ownership, reopen, abrupt child-process exit after commit, first-value and
  missing-value replay, changed bindings, corrupted/missing evidence, concurrent
  reads, rollback, read quotas and observer failure after commit.
- `tests/langgraph/test_native_memory.py`: actual SDK reader/principal/access
  checks, plus retained values and absence inside actual native workflow calls.
- `tests/langgraph/test_native_memory_provider.py`: actual SQLite/PostgreSQL
  setup/reopen/close, dependency/platform rejection before storage creation,
  setup bounds and errors, actual native workflow receipt integration, and
  explicit SQLite path/URL separation from the common database.
- `tests/langgraph/test_langgraph_memory_admission.py`: the existing closed
  capability and checkpoint admission regressions.

Linux uses dependency image
`sha256:591b1bc4959940d8c6d7fa4f5c5b2471ddf2d68570e04415cbc420981f5e9f42`
with unchanged root/adapter manifests and locks. The image is reused for pinned
dependencies; the frozen source is mounted read-only. The test root filesystem
is read-only, capabilities are dropped, and no-new-privileges is set. The runner
uses arbitrary UID 12347, 1 GiB memory, a 128-process ceiling and a 512 MiB
temporary filesystem. PostgreSQL 16.15 runs separately on the development
Docker network, using a fresh `owa_memory_0268ea1` test database. Unlike the
earlier 41-case reader run, this database cohort is networked. Nothing is
installed at startup. All **1,176 source inputs remain unchanged** after testing.

The separate existing effect-journal/ownership regression passes all **99 cases
with zero skips** after enabling its optional PostgreSQL selection. Its earlier
SQLite/default-only run passed 74 cases and skipped those 25 PostgreSQL cases.
Ruff, formatting and strict typing pass. XML, logs and input manifests are under
`tmp/merge-20261025/`, including `0268ea1/memory-linux-result.json` and
`memory-durable-windows-final.xml`. The preceding `e7a44fa` cohort passed 116
cases per platform before the explicit SQLite datasource correction added its
two regression cases. This is focused component evidence, not full
repository/release qualification or completion of S4-02.

### Fenced native transaction follow-up — `f76fed9`

Source `f76fed98c8e24f86066bfda5c61866c7841ad1b2` passes **169 selected cases
with zero skips on each of Windows and frozen Linux**. This includes the
earlier 118-case reader/provider/admission cohort, 36 SDK cases in
`tests/langgraph/test_native_memory_mutations.py` and 15 framework-neutral cases
in `tests/core/test_memory_mutations.py`:

- Actual SQLite/PostgreSQL put/delete and missing-item delete, repeat detection,
  another invocation's later write, changed values/bindings/bounds and damaged
  markers. Original principal, namespace and lease binding are required.
- Rejection of stale generations, including cached markers; rollback of both
  SDK data and marker after an exception following SDK SQL or lease expiry at
  the final guard; sanitized provider failures.
- Cancellation draining before cleanup, concurrent duplicate requests and two
  different writes competing for an invocation's last marker quota slot.
- Abrupt child-process exit after native commit, ownership takeover, a later
  logical write, and retry of the earlier operation without restoring old data.
- Finite/canonical record validation and copied caller data/descriptors.

Linux reuses the immutable dependency image and controls recorded above, with
matching dependency locks, a fresh `owa_memory_f76fed9` PostgreSQL database and
no installation at startup. All **1,180 frozen source inputs remain unchanged**.
Reports are `tmp/merge-20261025/f76fed9/memory-linux-result.json`, its adjacent
XML/log/manifest, and `memory-mutations-final-windows.xml`. Ruff, formatting
and strict typing pass. Eight shared authorization isolation/denial checks pass
on actual LangGraph with SQLite/PostgreSQL, with 44 unrelated cases deselected;
see `memory-mutations-shared-contract.xml`. That contract run used `f5a5bb3`;
runtime code is unchanged in the final test-only follow-up.

The preceding `f5a5bb3` cohort passed 163 cases per platform. The follow-up fixes
missing optional-SDK collection guards, moves framework-neutral checks into the
core suite and adds six original-invocation checks. In a core-only environment,
all 15 request tests pass and the two SDK test modules skip cleanly. No framework
dependency was added to core. Four inventory/tracking checks and relative
Markdown links pass; no inventory feature is promoted by these component checks.

This qualifies the private native transaction primitive and its provider
setup. It does not qualify common workflow write recovery, public authorization
or a native memory feature declaration. S4-02 and its related research rows
remain open; the overall verified task count stays 31 of 37.

### Common write recovery qualification — `54103b9`

Source `54103b92e9ffbb0f9b0a51f36d21337112d53bfc` passes **322 cases with zero
skips on each of Windows and frozen Linux**. The cohort includes the prior 169
memory cases, 40 common write-journal cases, 14 actual store/workflow recovery
cases, and 99 existing effect/ownership regression cases.

The new common cases cover prepared-intent restart, uncertainty after dispatch,
native commit without acknowledgement, service reopen, corruption/missing
records, phase/receipt rollback, cancellation, ownership takeover, duplicate
calls, quota enforcement and observer failure. Actual SQLite/PostgreSQL cases
cover authorized puts/deletes and repeated calls, policy denial before intent
or SDK mutation, original-principal isolation despite ambient substitution,
missing native proof blocking checkpoint resume, and provider reopen recovery.
The combined process test exits without cleanup after native commit and before
common completion; a new owner later reconciles the old operation without
overwriting a newer logical write.

Linux retains the same immutable pinned dependency image and controls recorded
above, using a fresh `owa_memory_54103b9` PostgreSQL database. All **1,184 frozen
source inputs remain unchanged** and nothing is installed at startup. Reports
are under `tmp/merge-20261025/54103b9/`, including `memory-linux-result.json`,
XML/log/manifest, plus `memory-write-recovery-qualified-windows.xml`.

The core-only selection passes 35 cases; its 20 optional PostgreSQL cases and
one SDK module skip as expected without those dependencies. Ruff, formatting,
strict typing, relative Markdown links and four inventory/tracking tests pass.
This is finite private-component qualification, not a full repository/release
run. S4-02 stays unchecked and no research capability is promoted.

## Remaining work before enabling the capability

- Define and validate the versioned workflow bindings, logical record/schema and
  retention semantics within the approved metadata extension boundary.
- Connect the private provider factories and read receipts to those validated
  workflow bindings without making external services mandatory.
- Add record/namespace retention without discarding proof needed by recoverable
  invocations. Define and qualify explicit resolution of uncertain dispatched
  writes. The private authorized writer now connects native transactions to
  common intents, completed receipts, native receipt observation and missing
  evidence rejection; versioned public workflow bindings remain open.
- Complete native-checkpoint/crash recovery, changed authority/policy,
  concurrent first-use/provider setup and shutdown-failure qualification on the
  required engines/stores/platforms before updating capability metadata.

Core remains responsible for common memory semantics, identity, loading,
expressions, admission and errors. Framework APIs and provider dependencies stay
inside the adapter. This work does not activate delegated identity/multi-tenancy
or the other conditional tracks.
