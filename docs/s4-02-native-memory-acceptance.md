# S4-02 native memory — private providers and repeatable reads

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
retention/deletion policy and native writes remain unimplemented.

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

## Remaining work before enabling the capability

- Define and validate the versioned workflow bindings, logical record/schema and
  retention semantics within the approved metadata extension boundary.
- Connect the private provider factories and read receipts to those validated
  workflow bindings without making external services mandatory.
- Implement native/common write ownership fencing, atomicity or reconciliation,
  write quotas, retention and recovery. Recovery must not apply an unowned or
  duplicate write. Reads are now covered by the finite observation profile.
- Complete native-checkpoint/crash recovery, changed authority/policy,
  concurrent first-use/provider setup and shutdown-failure qualification on the
  required engines/stores/platforms before updating capability metadata.

Core remains responsible for common memory semantics, identity, loading,
expressions, admission and errors. Framework APIs and provider dependencies stay
inside the adapter. This work does not activate delegated identity/multi-tenancy
or the other conditional tracks.
