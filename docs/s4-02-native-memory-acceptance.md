# S4-02 native memory — private reader component

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
The component constructs no provider, starts no process and installs nothing.

## Verification scope

The focused tests use actual pinned LangGraph stores (in-memory and optional
SQLite) and actual native workflow calls with common persisted authority.
They cover concurrent original-principal isolation despite identical caller
labels, copied JSON results, denial and deployment-disable before store access,
sanitized errors, asynchronous deadlines, invalid data/limits and the closed
required-feature gate. The SQLite case enables read-refresh by default and
checks the underlying store rows, including expiry, remain unchanged.

The Windows actual-SDK selection passes **41 cases**: the new reader tests plus
the existing native memory/checkpoint admission regressions. Ruff, formatting,
strict adapter typing and relative Markdown links pass. Linux focused execution
is the next check. Raw reports are under `tmp/merge-20261025/`.
These tests do not establish a public memory API, durable memory operations or
full native store parity.

## Remaining work before enabling the capability

- Define and validate the versioned workflow bindings, logical record/schema and
  retention semantics within the approved metadata extension boundary.
- Add deployment provider selection and actual SQLite/PostgreSQL/native SDK
  dependency admission without making external services mandatory.
- Integrate common durable read receipts and native/common write ownership
  fencing, quotas, retention and recovery behavior. Repeated recovery must not
  silently re-read changed data or apply an unowned/duplicate write.
- Prove service/process restart, changed authority/policy, provider failures,
  concurrent access and shutdown on required engines/stores/platforms before
  updating capability metadata.

Core remains responsible for common memory semantics, identity, loading,
expressions, admission and errors. Framework APIs and provider dependencies stay
inside the adapter. This work does not activate delegated identity/multi-tenancy
or the other conditional tracks.
