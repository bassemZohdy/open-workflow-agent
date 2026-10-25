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

## Private record expiry follow-up

`MemoryRecordRetention` is an immutable deployment-side policy with a bounded
ID/version and an integer TTL of 1 through 31,536,000 seconds. Its exact profile
is `owa/memory-record-retention/v1`; unknown, incomplete or untyped metadata
rejects. The optional policy is carried in the private mutation descriptor and
request digest. Omitting it preserves the previous descriptor and digest.
No public workflow schema or capability declaration changes.

Provider setup binds the policy to the installed storage identity. Changing
policy on a live binding rejects before storage setup; opening a fresh runtime
with a different policy rejects recovery of old write receipts. The private
writer supplies the installed policy, so record content cannot choose TTL or
an expiry timestamp. The default remains no expiry for new writes.

For an opted-in put, the native transaction uses database time to update the
SDK record's existing expiry column after the pinned SDK writes the value.
The value, deadline and immutable native marker commit together under the
original ownership fence. If the deadline update fails or affects no row,
everything rolls back. Matching common or native retries skip the write and
do not renew its deadline. No SDK TTL sweeper or read refresh is enabled.

Factory get adapters omit expired rows before SDK deserialization/projection.
SQLite adds the missing expiry predicate to the pinned SDK's prepared get
query; PostgreSQL enables its existing omit-expired behavior. This also avoids
loading an expired oversized record into the public projection. Reads use
database time, independently of workflow input and process clock/timezone.

Expiry governs **fresh get observations**. An already recorded observation
continues replaying its original value or absence. Native markers and common
intents/completion receipts remain valid after the record expires. This keeps
recoverable workflows from silently changing their past results.

This is **logical record expiry**: the physical SDK row, common write payload
and historical read observations are not erased by this policy. Physical
cleanup, namespace storage quotas and independent invocation/history retention
remain separate acceptance work. Raw SDK deployment/admin access is not a
public workflow interface; no background sweep is started here.

## Private namespace capacity and physical cleanup follow-up

`MemoryNamespaceBounds` defines an exact `owa/memory-namespace-bounds/v1`
deployment policy with a fixed ID/version, 1–10,000 records, 1–67,108,864
serialized-value bytes, and a cleanup batch of 1–64 records. The private mutation
descriptor and provider identity include that policy. Default omission preserves
the earlier descriptor and provider digest. Workflow input cannot choose scope
or raise the limits.

Capacity applies to the exact principal/collection namespace. It counts physical
SDK rows, including expired rows, and UTF-8 bytes of the backend's serialized
value representation. SQLite storage bytes and PostgreSQL JSONB text may differ;
these are provider storage units, not a portable canonical-JSON size or a bound
on database files, indexes, logs or retained workflow history. The bounded
aggregate inspects at most `max_records + 1` rows and returns only totals.
An over-budget put rolls back its value, expiry and marker together. Replacing
an existing key is allowed if the resulting namespace fits; explicit deletion
can restore capacity without requiring an already over-budget namespace to fit.

The first bounded mutation seals the namespace policy in a native metadata row.
Existing unsealed legacy data is not silently adopted, and a lost seal with data
still present fails closed. Unbounded or differently configured writers cannot
bypass an existing seal. Every new PostgreSQL mutation takes a namespace lock,
including unbounded callers; SQLite's native writer transaction supplies the
same exclusion. The existing execution fence also protects namespace metadata.

The private writer's `sweep_expired()` requires `memory.delete`, the original
principal authority and installed namespace bounds. It selects only expired
keys in the fixed collection, ordered by expiry and key, up to `cleanup_limit`.
Selection and the pinned SDK delete batch share the owned native transaction
and namespace lock. PostgreSQL also locks selected rows against direct SDK
updates. Cleanup verifies those keys are absent before recording completion.
It therefore cannot delete a record refreshed by a concurrent writer between
selection and deletion. A silent failure, exception
or ownership loss aborts the transaction; common dispatch ambiguity retains its
existing conservative rules.

One sweep is one immutable logical operation. Repeating it acknowledges its
original batch and does not delete records that expire or reappear later. A new
logical occurrence/invocation can perform the next batch. The API returns no
native identifier or deleted-key list. Native markers, common write intents and
read observations are not removed, so old operations still reconcile after their
data records are physically gone. There is no global or background sweep.

Scope permission checks now match the portable memory tools. Multiple distinct
writes in one catalog action reuse the same principal-scope permission; each
write's contents remain independently bound by its common intent and native
marker. Changed contents for the same logical record slot still reject.

## Private store setup and shutdown follow-up

The provider factory serializes the complete pinned SDK setup plus OWA metadata
setup across workers. PostgreSQL uses a schema-scoped advisory session lock on
the factory's unpooled connection. Short nonblocking probes avoid holding a
waiting snapshot while the SDK runs `CREATE INDEX CONCURRENTLY`; a transaction
cannot wrap that SDK migration. SQLite uses a separate adjacent
`<native-database>.setup.sqlite3` lock database, so SDK `executescript()` commits
cannot release exclusion early. The lock file persists but connection/process
exit releases the database lock. Separate `:memory:` stores need no shared lock.
These are deployment setup controls, not new workflow execution paths.

The existing setup deadline bounds lock contention. Cancellation and timeout
close the contender's connection, and process exit releases either backend's
lock. Setup continues to use SDK migration code and version rows; no migrations
or dependency installation are performed by workflow calls. Normal shutdown
failures remain sanitized and bounded. When the caller is already failing or
cancelled, cleanup preserves that original exception.

## Private explicit write resolution

`MemoryWrites.resolve` is a trusted, preauthorized control primitive for an
already dispatched write in a running invocation. It requires the exact
original request, installed backend/storage policy, original principal and live
execution lease. It never invokes the backend's write callback. A versioned
`owa/memory-write-resolution/v1` decision has a bounded operation ID and two
choices:

- `complete` requires a matching native marker and records the original write's
  common completion without replaying the write.
- `abandon_unknown` requires explicit `acknowledge_unknown: true` and no matching
  marker at decision time. It records a permanent fault whose outcome is still
  **unknown**. It promises neither nonexecution, rollback nor compensation.

The decision, its common identity index, mutation phase and resulting receipt
commit in one owned transaction. There is at most one immutable choice for each
logical write. Repeating that choice returns the existing audit; changing its
operation ID, principal or outcome rejects. An unadmitted/prepared write cannot
be resolved, and an automatically completed write cannot be relabeled.

Abandoned writes replay the same common memory error after observing their
fault receipt; they never dispatch again. A late matching native marker does
not rewrite the acknowledged unknown outcome or the operator's decision.
Retained audit evidence contains request/storage digests and common identity,
not the record value, credentials or a native checkpoint. Recovery checks
audit/index/phase/outcome consistency in both directions. Missing or altered
evidence fails closed. Completion still requires retained native proof.

This primitive does not authenticate its caller or expose an HTTP endpoint,
catalog function or agent tool. Those are separate pending public-control and
binding work: ingress must authenticate the caller, enforce original-principal
access and authorize the resolution before using this private method. It does
not reopen terminal invocations or add a force-retry/force-success operation.

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

### Record expiry qualification — `810c723`

Source `810c723ba21b104076516449a7d17af4cf9dd070` passes **355 selected cases
with zero skips on each of Windows and frozen Linux**. This includes the prior
322-case memory/journal/ownership cohort, 21 new core policy/serialization cases
and 12 actual SQLite/PostgreSQL expiry cases. Existing mutation fault-injection
tests target the pinned SDK base class beneath the new get-filter adapter.

The new cases cover strict policy bounds/version metadata, unchanged default
descriptors/digests, database-clock deadlines, expired fresh reads, unchanged
replayed observations and retry deadlines, filtering of oversized expired
records, rollback after deadline SQL, silent suppression of the deadline update,
and changed-policy rejection before live setup or cached write recovery. All
provider/model work is deterministic and uses no paid API.

Linux reuses the same immutable dependency image and controls recorded above,
with matching locks and a fresh `owa_memory_810c723` PostgreSQL database. All
**1,188 frozen source inputs remain unchanged**, with no startup installation.
Reports are in `tmp/merge-20261025/810c723/` and
`memory-record-expiry-qualified-windows.xml`. The core-only selection passes
36 cases and skips one optional SDK module. Ruff, formatting, strict typing,
relative Markdown links and four inventory/tracking checks pass.
Eight shared LangGraph SQLite/PostgreSQL authority isolation/denial checks also
pass at this source, with 44 unrelated cases deselected; see
`memory-record-expiry-shared-contract.xml` in the same evidence directory.

This is logical get-expiry qualification, not physical purge, namespace-capacity
enforcement or full repository/release qualification. Historical common payloads
and proof remain retained. S4-02 stays unchecked; public native memory admission
remains closed.

### Namespace capacity and cleanup qualification — `6a0ac8c`

Source `6a0ac8cc0a3a17cd3b34573e3ca430a80bcb634d` passes **399 cases with zero
skips on each of Windows and frozen Linux**: the prior 355-case cohort, 20 new
core policy/descriptor cases and 24 actual SQLite/PostgreSQL capacity/cleanup
cases. These cover record and byte budgets, replacement rollback, concurrent
last-slot admission, bounded cleanup, original authority and collection scope,
permission denial, repeated sweeps, later refreshed records, preserved recovery
proof, SDK failures, silently suppressed deletes, lost policy seals and unbounded
writer rejection. Multiple distinct writes in one workflow action succeed while
changed contents for the same logical operation still reject.

Linux uses the immutable dependency image and restrictions recorded above,
matching locks and a fresh `owa_memory_6a0ac8c` PostgreSQL database. All **1,192
frozen source inputs remain unchanged**; nothing is installed at startup. Reports
are under `tmp/merge-20261025/6a0ac8c/` and
`memory-capacity-final-qualified-windows.xml`.

Eight shared LangGraph SQLite/PostgreSQL authority isolation/denial checks pass
at this runtime source, with 44 unrelated cases deselected; see
`memory-capacity-shared-contract.xml`. The core-only policy/request selection
passes 56 cases and skips one optional SDK module. Relative Markdown links and
four inventory/tracking checks pass. Ruff, formatting and strict typing pass.

The preceding `b8a5d07` cohort passed 397 cases on each platform. A subsequent
fault test reproduced a false cleanup acknowledgement when a database trigger
quietly skipped deletion. The final source adds row locking and an absence check;
both stores now reject that case and roll back the marker. An earlier workflow
test exposed content-dependent parent permission reuse; the corrected writer
uses the same scope permission identity as portable memory tools, retaining
per-write immutable intent checks. Earlier failed reports remain alongside the
successful results. This is finite private-component evidence, not full
repository/release qualification or S4-02 completion.

### Provider setup and lifecycle qualification — `738b393`

Source `738b3935fb59442c82e8e4171717ec4e2b7da47d` passes **415 cases with zero
skips on each of Windows and frozen Linux**, comprising the prior 399-case cohort and 16 new
lifecycle cases on actual SQLite/PostgreSQL. These include three simultaneous
first-use connections, successful reopen with all records, bounded contended
setup and cancellation, abrupt child-process exit while holding the setup lock,
and shutdown error/timeout injection after real connection closure. Caller
errors and cancellation preserve their original identity. These are finite
component witnesses; they do not establish every migration-fault, deployment
filesystem or shutdown-resource-failure scenario.

The original first-use test failed on PostgreSQL with two of three starts
rejected. Wrapping SDK setup with a blocking advisory query still failed; short
nonblocking probes avoid retaining a waiting snapshot during concurrent index
creation. The original failure and follow-up reports remain under
`tmp/merge-20261025/`, alongside `memory-lifecycle-final-focused.xml`.

Linux reuses the immutable image and controls recorded above, with matching
locks, a fresh `owa_memory_738b393` PostgreSQL database and **1,194 unchanged
source inputs**. Reports, XML, logs and manifest are in the `738b393/` evidence
directory; Windows uses `memory-lifecycle-qualified-windows.xml`. The core-only
policy/request selection passes 56 cases and skips one
optional SDK module; four inventory/tracking checks and Markdown links pass.
Ruff, formatting and strict typing pass. Eight shared LangGraph SQLite/PostgreSQL
authority isolation/denial checks pass, with 44 unrelated cases deselected; see
`memory-lifecycle-shared-contract.xml`. Public memory admission remains closed and
S4-02 remains open.

### Explicit resolution qualification — `74294b5`

Source `74294b541b126ed7fc1d725c43264551dd4f7cbf` passes **475 cases with zero
skips on each of Windows and frozen Linux**: the prior 415-case cohort, 52 new common resolution
cases and eight actual SQLite/PostgreSQL resolution cases. These cover exact
typed acknowledgement, matching/missing native proof, immutable repeat decisions,
service reopen, original request/principal, prepared/terminal-state rejection,
stale and expiring leases, atomic audit/outcome rollback, damaged or missing
audit/index/phase evidence, and late native evidence after abandonment. Actual
store cases preserve later values and survive child-process exit immediately
after resolution commit, without normal cleanup. No resolution dispatches a
native write.

Linux reuses the immutable dependency image and controls recorded above, with
matching locks, a fresh `owa_memory_74294b5` PostgreSQL database and **1,197
unchanged source inputs**. Reports, XML, log and manifest are under
`tmp/merge-20261025/74294b5/`; Windows uses
`memory-resolution-qualified-windows.xml`. The focused new selection also passes
60 cases on Windows. Eight shared LangGraph SQLite/PostgreSQL authority
isolation/denial checks pass, with 44 unrelated cases deselected; see
`memory-resolution-shared-contract.xml`.
Core-only resolution checks pass 32 cases, skip 20 optional PostgreSQL cases
and one optional SDK module; four inventory checks also pass. Ruff, formatting,
strict typing and Markdown links pass. The initial core test used a nonexistent
test-helper method to persist cancellation; its corrected case now uses the
existing invocation transition API. The failed report is retained.

This qualifies private control and evidence semantics. It does not qualify
public resolution authorization, API discovery/control or workflow bindings,
reopen terminal invocations, promote any research row or complete S4-02.

## Remaining work before enabling the capability

- Define and validate the versioned workflow bindings, logical record/schema and
  retention semantics within the approved metadata extension boundary.
- Connect the private provider factories and read receipts to those validated
  workflow bindings without making external services mandatory.
- Qualify the complete public binding profile over the private namespace bounds,
  bounded physical cleanup and logical expiry, preserving recovery proof.
  Connect explicit resolution of uncertain dispatched writes to authenticated,
  authorized public controls and qualify that complete boundary. The private
  control primitive above records proof-based completion or acknowledged unknown
  abandonment. The private authorized writer connects native transactions to
  common intents, completed receipts, native receipt observation and missing
  evidence rejection; versioned public workflow bindings remain open.
- Complete the public profile's native-checkpoint/crash recovery and changed
  authority/policy qualification on the required engines/stores/platforms.
  The finite first-use/provider setup and shutdown witnesses above do not
  replace complete public workflow qualification before capability changes.

Core remains responsible for common memory semantics, identity, loading,
expressions, admission and errors. Framework APIs and provider dependencies stay
inside the adapter. This work does not activate delegated identity/multi-tenancy
or the other conditional tracks.
