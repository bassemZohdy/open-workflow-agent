# Configured native memory

This opt-in LangGraph profile installs fixed deployment bindings and ordinary
Open Workflow 1.0.3 custom catalog functions. It requires durable persistence,
the native LangGraph runtime and original execution authorization. SQLite is
embedded by default; PostgreSQL is optional. Existing agent memory tools retain
their separate portable contract. S4-02 remains open, including semantic search.
See the [acceptance record](s4-02-native-memory-acceptance.md) for source-specific
verification and remaining limits. This profile is not yet in a published image.

Mount [native-memory.yaml](../examples/native-memory.yaml) as `/config/agent.yaml`
and [its workflow](../examples/native-memory-workflow.yaml) at the configured path
in a LangGraph build containing this feature. Supply `OWA_NOTES_TOKEN` through
deployment secrets and retain `/data`. No application code or external database
is required. Invoke the workflow with `{}` and the configured bearer token.
The second task returns `{"items":[{"key":"favorite-color","value":{"color":"blue","kind":"preference"}}]}`.
Another invocation by the same authenticated principal can read those records;
user/session labels never select the namespace.

The workflow must require `langgraph.memory@1.0.0` in its existing native
extension metadata. Each configured binding installs the following exact
references, where `notes` is the deployment binding ID:

| Function | Arguments | Result |
| --- | --- | --- |
| `memory.get:1.0.0@notes` | `key` | `{"value": object-or-null}` |
| `memory.put:1.0.0@notes` | `key`, object `value` | `{"ok":true}` |
| `memory.search:1.0.0@notes` | optional object `filter`, optional integer `limit` | `{"items":[{"key":string,"value":object}]}` |
| `memory.delete:1.0.0@notes` | `key` | `{"ok":true}` |
| `memory.sweep:1.0.0@notes` | empty object | `{"ok":true}` |

Unknown fields, caller-supplied namespaces/providers, required unsupported
versions and undeclared bindings fail admission or bounded argument validation.
Replacing an installed handler invalidates its capability and execution. ADK and minimal installations
reject this required capability. `catalog.call` on the exact reference is
required in addition to `memory.search`, `memory.add` or `memory.delete` on
`memory:self`. Every reuse checks the original authority and retained decision.

Search matches at most 16 top-level scalar fields by equality with explicit
JSON types. Null matches an explicit null field; it does not match an absent
field. Numbers compare numerically, while booleans and strings remain distinct.
Filters have a 4,096-byte bound. Results use ascending key order, exact namespace
equality and a deployment-capped limit of at most 64. There is no offset,
substring/operator filter, vector query, relevance ranking or semantic search.
Expired records are removed from fresh query results before SDK deserialization;
reads do not refresh expiry. Query values are not passed to SDK debug SQL logging.

Bindings cap record bytes (at most 65,536), live records, live serialized-value
bytes and physical cleanup batches. Optional database-clock retention applies
when writing. Search's entire projected result must fit the binding's record-byte
bound. These bounds apply to the common JSON projection, not hard process memory
or the SDK's initial record loading. Read deadlines cancel the awaiting call;
they do not promise to stop an internal SDK batch already executing.

Read observations and write intents each have a ceiling of 64 per invocation.
Replay retains the first committed read/search result, including absence, even
after later writes or expiry. Completed public results require matching common
leaf evidence even before a native checkpoint has saved its receipt link. A
committed native write with a missing common acknowledgement is reconciled from
its marker. An uncertain dispatched write pauses the workflow and is never
blindly repeated. Ordinary custom functions retain their existing recovery rules.

## Resolve a paused write

An authenticated original owner with `memory.inspect` on the invocation can
read `GET /v1/invocations/{invocation_id}/memory-writes`. The bounded response
contains operation IDs, request digests, task references, occurrence, binding,
key, kind and common state. It contains no values, provider locations or SDK IDs.
`verification: common_metadata_only` does not attest that native execution happened.

With `memory.resolve` on the invocation and under its original `memory:self`
authority, call
`POST /v1/invocations/{invocation_id}/memory-writes/{operation_id}/resolve`.
Supply an `Idempotency-Key` of 1–128 letters, digits, underscores, periods or
hyphens (starting with a letter or digit) and this body:

```json
{"request_digest":"<digest from inspection>","decision":"complete"}
```

`complete` requires matching retained native proof. If that proof is absent,
an explicit decision may abandon the write with unknown outcome:

```json
{"request_digest":"<digest from inspection>","decision":"abandon_unknown","acknowledge_unknown":true}
```

Abandonment is not proof that execution never happened. The control records a
protected authorization receipt and atomically commits its immutable decision,
phase and common outcome. Neither decision dispatches SDK writes. A conflicting
choice, missing proof, wrong digest, lost ownership or changed binding fails.
Repeat the same request/key to acknowledge the existing decision. Then call the
normal invocation resume API: completion returns the original write result;
abandonment faults with a common memory error whose outcome is `unknown`.

First decisions require a running or waiting invocation. Terminal invocations
can acknowledge an identical existing decision but cannot create a first one
or reopen execution. Recovery controls are absent when native memory is not
configured. Delegated operators and multi-tenancy remain conditional backlog work.
